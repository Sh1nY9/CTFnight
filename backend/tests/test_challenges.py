from __future__ import annotations

import uuid

import pytest
from conftest import create_admin, login, mutate, register
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import alpha.routes_participant as participant_routes
import alpha.services as service_module
from alpha.models import (
    AuditEvent,
    Challenge,
    Event,
    Membership,
    OutboxEvent,
    ScoreEvent,
    Solve,
    Submission,
    Team,
    User,
    utcnow,
)
from alpha.security import hash_flag
from alpha.services import current_event, dynamic_points
from alpha.store import RateLimitResult


def admin_login(ctx):
    create_admin(ctx)
    response = login(ctx.client, "admin@example.com", "AdminPassword!123")
    assert response.status_code == 200


def set_live(ctx):
    response = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "live"})
    assert response.status_code == 200


def create_challenge(ctx, **overrides):
    payload = {
        "slug": "welcome",
        "title": "Welcome",
        "category": "Misc",
        "description_md": "Find the flag.",
        "connection_info": None,
        "scoring_type": "fixed",
        "initial_points": 100,
        "minimum_points": 100,
        "decay": 20,
        "visible": True,
        "max_attempts": 0,
        "prerequisite_ids": [],
        "flag": {"type": "exact", "value": "FLAG{welcome}"},
    }
    payload.update(overrides)
    response = mutate(ctx.client, "POST", "/api/v1/admin/challenges", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def prepare_player(ctx, username="player", team="Red"):
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert register(ctx.client, username).status_code == 201
    response = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": team})
    assert response.status_code == 201


def activate_for_player(ctx, username="player"):
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    set_live(ctx)
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, f"{username}@example.com", "CorrectHorse!123").status_code == 200


def test_exact_flag_submission_idempotency_scoreboard_and_no_secret_storage(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    assert "flag" not in challenge
    prepare_player(ctx)
    activate_for_player(ctx)

    listed = ctx.client.get("/api/v1/challenges")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["current_points"] == 100
    challenge_id = challenge["id"]
    wrong = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge_id}/submit",
        json={"flag": "FLAG{wrong}", "idempotency_key": "wrong-0001"},
    )
    assert wrong.status_code == 200 and wrong.json()["correct"] is False
    solved = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge_id}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "solve-0001"},
    )
    assert solved.status_code == 200
    assert solved.json()["correct"] is True
    assert solved.json()["awarded_points"] == 100
    replay = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge_id}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "solve-0001"},
    )
    assert replay.json() == solved.json()

    scoreboard = ctx.client.get("/api/v1/scoreboard").json()
    assert scoreboard["entries"][0]["team_name"] == "Red"
    assert scoreboard["entries"][0]["score"] == 100
    cannot_leave = mutate(ctx.client, "POST", "/api/v1/teams/leave")
    assert cannot_leave.status_code == 409
    assert cannot_leave.json()["error"]["code"] == "team_changes_closed"
    with ctx.database.session_factory() as db:
        stored = db.get(Challenge, uuid.UUID(challenge_id))
        assert stored.flag_hash == hash_flag(ctx.settings.secret_key.get_secret_value(), "FLAG{welcome}")
        assert stored.flag_hash != "FLAG{welcome}"
        assert db.scalar(select(func.count()).select_from(Solve)) == 1
        assert db.scalar(select(func.count()).select_from(Submission)) == 2
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "challenge.solved")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "challenge.solved")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == "submission.incorrect")
            )
            == 0
        )


def test_dynamic_score_records_each_solve_value_without_rewriting_history(ctx, monkeypatch):
    admin_login(ctx)
    challenge = create_challenge(
        ctx,
        slug="dynamic",
        title="Dynamic",
        scoring_type="dynamic",
        initial_points=500,
        minimum_points=100,
        decay=10,
        flag={"type": "exact", "value": "FLAG{dynamic}"},
    )
    prepare_player(ctx, "one", "One")
    prepare_player(ctx, "two", "Two")
    activate_for_player(ctx, "one")
    first = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{dynamic}", "idempotency_key": "solve-one"},
    )
    assert first.json()["awarded_points"] == dynamic_points(500, 100, 10, 1)

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "two@example.com", "CorrectHorse!123").status_code == 200
    second = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{dynamic}", "idempotency_key": "solve-two"},
    )
    expected = dynamic_points(500, 100, 10, 2)
    assert second.json()["awarded_points"] == expected
    board = ctx.client.get("/api/v1/scoreboard").json()
    assert [entry["score"] for entry in board["entries"]] == [expected, expected]
    assert board["total_entries"] == 2
    assert board["truncated"] is False
    with ctx.database.session_factory() as db:
        points = list(db.scalars(select(ScoreEvent.points).order_by(ScoreEvent.created_at, ScoreEvent.id)))
        assert points == [dynamic_points(500, 100, 10, 1), expected]
        monkeypatch.setattr(service_module, "MAX_PUBLIC_SCOREBOARD_ENTRIES", 1)
        bounded = service_module.public_scoreboard(db, current_event(db))
        assert bounded["total_entries"] == 2
        assert bounded["truncated"] is True
        assert len(bounded["entries"]) == 1


def test_incorrect_submission_does_not_evict_public_scoreboard_cache(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)

    assert ctx.client.get("/api/v1/scoreboard").status_code == 200
    with ctx.database.session_factory() as db:
        event_id = db.scalar(select(Event.id))
    cache_key = f"scoreboard:{event_id}:public:live"
    assert ctx.client.app.state.store.get_json(cache_key) is not None

    wrong = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{wrong}", "idempotency_key": "cache-wrong"},
    )
    assert wrong.status_code == 200 and wrong.json()["correct"] is False
    assert ctx.client.app.state.store.get_json(cache_key) is not None

    correct = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "cache-correct"},
    )
    assert correct.status_code == 200 and correct.json()["correct"] is True
    assert ctx.client.app.state.store.get_json(cache_key) is None


def test_scoreboard_cache_lease_and_generation_are_fail_closed(ctx, monkeypatch):
    admin_login(ctx)
    create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    with ctx.database.session_factory() as db:
        event_id = db.scalar(select(Event.id))

    store = ctx.client.app.state.store
    cache_key = f"scoreboard:{event_id}:public:live"
    generation_key = f"scoreboard:{event_id}:generation"
    lease_key = f"scoreboard:{event_id}:build:live"
    lease_token = store.acquire_lease(lease_key, 45)
    assert lease_token is not None
    busy = ctx.client.get("/api/v1/scoreboard")
    assert busy.status_code == 503
    assert busy.json()["error"]["code"] == "scoreboard_busy"
    assert busy.headers["Retry-After"] == "1"
    assert store.release_lease(lease_key, "not-the-owner") is False
    assert store.release_lease(lease_key, lease_token) is True

    calls = 0
    real_builder = participant_routes.public_scoreboard

    def tracked_builder(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(participant_routes, "public_scoreboard", tracked_builder)
    assert ctx.client.get("/api/v1/scoreboard").status_code == 200
    assert ctx.client.get("/api/v1/scoreboard").status_code == 200
    assert calls == 1
    store.increment(generation_key)
    assert ctx.client.get("/api/v1/scoreboard").status_code == 200
    assert calls == 2

    store.delete(cache_key)

    def invalidating_builder(*args, **kwargs):
        result = real_builder(*args, **kwargs)
        store.increment(generation_key)
        return result

    monkeypatch.setattr(participant_routes, "public_scoreboard", invalidating_builder)
    stale = ctx.client.get("/api/v1/scoreboard")
    assert stale.status_code == 503
    assert stale.json()["error"]["code"] == "scoreboard_changed"
    assert store.get_json(cache_key) is None
    released_token = store.acquire_lease(lease_key, 45)
    assert released_token is not None
    assert store.release_lease(lease_key, released_token) is True


def test_prerequisites_and_regex_flags(ctx):
    admin_login(ctx)
    first = create_challenge(ctx)
    second = create_challenge(
        ctx,
        slug="regex",
        title="Regex",
        initial_points=200,
        minimum_points=200,
        prerequisite_ids=[first["id"]],
        flag={"type": "regex", "value": r"^FLAG\{[0-9]{4}\}$"},
    )
    assert second["flag_type"] == "regex"
    assert "FLAG" not in str(second)
    prepare_player(ctx)
    activate_for_player(ctx)
    assert [item["slug"] for item in ctx.client.get("/api/v1/challenges").json()["items"]] == ["welcome"]
    mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{first['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "first-win"},
    )
    assert {item["slug"] for item in ctx.client.get("/api/v1/challenges").json()["items"]} == {
        "welcome",
        "regex",
    }
    result = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{second['id']}/submit",
        json={"flag": "FLAG{2026}", "idempotency_key": "regex-win"},
    )
    assert result.json()["correct"] is True


def test_attempt_limit_and_submission_rate_limit(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx, max_attempts=1)
    prepare_player(ctx)
    activate_for_player(ctx)
    mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "wrong", "idempotency_key": "attempt-one"},
    )
    limited = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "attempt-two"},
    )
    assert limited.status_code == 409
    assert limited.json()["error"]["code"] == "attempt_limit_reached"

    ctx.settings.submission_rate_limit = 1
    other_id = uuid.uuid4()
    rate_limited = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{other_id}/submit",
        json={"flag": "wrong", "idempotency_key": "attempt-three"},
    )
    assert rate_limited.status_code == 429


def test_submission_storage_caps_preserve_idempotency_and_write_no_incorrect_outbox(ctx, monkeypatch):
    monkeypatch.setattr(participant_routes, "MAX_CHALLENGE_ATTEMPTS", 2)
    monkeypatch.setattr(participant_routes, "MAX_SUBMISSIONS_PER_TEAM_EVENT", 3)
    admin_login(ctx)
    first = create_challenge(ctx)
    second = create_challenge(ctx, slug="second", title="Second")
    prepare_player(ctx)
    activate_for_player(ctx)

    first_results = []
    for index in range(2):
        first_results.append(
            mutate(
                ctx.client,
                "POST",
                f"/api/v1/challenges/{first['id']}/submit",
                json={"flag": f"wrong-{index}", "idempotency_key": f"hard-cap-{index}"},
            )
        )
    assert all(response.status_code == 200 for response in first_results)

    replay = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{first['id']}/submit",
        json={"flag": "different-body", "idempotency_key": "hard-cap-1"},
    )
    assert replay.status_code == 200
    assert replay.json() == first_results[1].json()

    challenge_limited = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{first['id']}/submit",
        json={"flag": "wrong-cap", "idempotency_key": "hard-cap-2"},
    )
    assert challenge_limited.status_code == 409
    assert challenge_limited.json()["error"]["code"] == "submission_storage_limit_reached"

    event_last = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{second['id']}/submit",
        json={"flag": "wrong-event", "idempotency_key": "event-cap-1"},
    )
    assert event_last.status_code == 200
    event_limited = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{second['id']}/submit",
        json={"flag": "wrong-event-2", "idempotency_key": "event-cap-2"},
    )
    assert event_limited.status_code == 409
    assert event_limited.json()["error"]["code"] == "submission_storage_limit_reached"

    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == "submission.incorrect")
            )
            == 0
        )


def test_incorrect_regex_uses_team_lock_without_global_challenge_lock(ctx, monkeypatch):
    admin_login(ctx)
    challenge = create_challenge(
        ctx,
        slug="regex-lock",
        title="Regex Lock",
        flag={"type": "regex", "value": r"^FLAG\{[0-9]{4}\}$"},
    )
    prepare_player(ctx)
    activate_for_player(ctx)

    real_scalar = Session.scalar
    locked_entities: list[type] = []

    def tracked_scalar(self, statement, *args, **kwargs):
        if getattr(statement, "_for_update_arg", None) is not None:
            locked_entities.extend(
                item["entity"]
                for item in getattr(statement, "column_descriptions", [])
                if item.get("entity") is not None
            )
        return real_scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", tracked_scalar)
    wrong = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "not-a-match", "idempotency_key": "regex-wrong-lock"},
    )
    assert wrong.status_code == 200
    assert wrong.json()["correct"] is False
    assert User in locked_entities
    assert Membership in locked_entities
    assert Team in locked_entities
    assert Challenge not in locked_entities

    locked_entities.clear()
    correct = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{2026}", "idempotency_key": "regex-right-lock"},
    )
    assert correct.status_code == 200
    assert correct.json()["correct"] is True
    assert Challenge in locked_entities


def test_team_rate_denial_does_not_consume_shared_submission_buckets(ctx, monkeypatch):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    calls: list[str] = []

    def deny_team(key: str, _limit: int, _window: int) -> RateLimitResult:
        calls.append(key)
        return RateLimitResult(False, 9) if ":team:" in key else RateLimitResult(True)

    monkeypatch.setattr(ctx.client.app.state.store, "check_rate", deny_team)
    rejected = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{wrong}", "idempotency_key": "tiered-submit"},
    )
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "9"
    assert len(calls) == 1 and ":team:" in calls[0]
    assert not any(":ip:" in key or ":challenge:" in key for key in calls)


def test_submission_ip_limit_is_shared_across_teams(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx, "one", "One")
    prepare_player(ctx, "two", "Two")
    activate_for_player(ctx, "one")
    ctx.settings.submission_ip_rate_limit = 1

    first = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "wrong-one", "idempotency_key": "shared-ip-one"},
    )
    assert first.status_code == 200

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "two@example.com", "CorrectHorse!123").status_code == 200
    second = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "wrong-two", "idempotency_key": "shared-ip-two"},
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "submission_rate_limited"


def test_database_unique_constraint_protects_concurrent_duplicate_solve(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    solved = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "first-solve"},
    )
    assert solved.json()["correct"] is True
    with ctx.database.session_factory() as db:
        original = db.scalar(select(Solve))
        duplicate_submission = Submission(
            team_id=original.team_id,
            challenge_id=original.challenge_id,
            user_id=original.user_id,
            submitted_hash="0" * 64,
            correct=True,
            idempotency_key="parallel-collision",
            ip_hash="1" * 64,
        )
        db.add(duplicate_submission)
        db.flush()
        db.add(
            Solve(
                team_id=original.team_id,
                challenge_id=original.challenge_id,
                user_id=original.user_id,
                submission_id=duplicate_submission.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Solve)) == 1


def test_invalid_regex_and_prerequisite_cycle_are_rejected(ctx):
    admin_login(ctx)
    first = create_challenge(ctx)
    invalid_regex = mutate(
        ctx.client,
        "POST",
        "/api/v1/admin/challenges",
        json={
            "slug": "bad-regex",
            "title": "Bad Regex",
            "category": "Misc",
            "description_md": "Invalid regex fixture.",
            "flag": {"type": "regex", "value": "(["},
        },
    )
    assert invalid_regex.status_code == 422
    assert invalid_regex.json()["error"]["code"] == "invalid_flag_regex"
    second = create_challenge(
        ctx,
        slug="second",
        title="Second",
        prerequisite_ids=[first["id"]],
    )
    cycle = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/challenges/{first['id']}",
        json={"prerequisite_ids": [second["id"]]},
    )
    assert cycle.status_code == 422
    assert cycle.json()["error"]["code"] == "prerequisite_cycle"


def test_admin_update_visibility_and_inventory_views(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    updated = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/challenges/{challenge['id']}",
        json={
            "title": "Updated Welcome",
            "initial_points": 250,
            "minimum_points": 250,
            "flag": {"type": "exact", "value": "FLAG{updated}"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated Welcome"
    assert "FLAG{updated}" not in updated.text
    hidden = mutate(
        ctx.client,
        "POST",
        f"/api/v1/admin/challenges/{challenge['id']}/visibility",
        json={"visible": False},
    )
    assert hidden.json()["visible"] is False
    mutate(
        ctx.client,
        "POST",
        f"/api/v1/admin/challenges/{challenge['id']}/visibility",
        json={"visible": True},
    )
    prepare_player(ctx)
    activate_for_player(ctx)
    wrong = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{not-updated}", "idempotency_key": "updated-wrong"},
    )
    assert wrong.json()["correct"] is False
    result = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{updated}", "idempotency_key": "updated-solve"},
    )
    assert result.json()["awarded_points"] == 250
    with ctx.database.session_factory() as db:
        rows = list(
            db.scalars(select(Submission).where(Submission.challenge_id == uuid.UUID(challenge["id"])))
        )
        shared_created_at = utcnow()
        for row in rows:
            row.created_at = shared_created_at
        db.commit()
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    submissions = ctx.client.get("/api/v1/admin/submissions").json()["items"]
    assert len(submissions) == 2
    assert submissions[0]["challenge"]["title"] == "Updated Welcome"
    assert "FLAG{updated}" not in str(submissions)
    first_page = ctx.client.get("/api/v1/admin/submissions", params={"limit": 1}).json()["items"]
    assert len(first_page) == 1
    second_page = ctx.client.get(
        "/api/v1/admin/submissions",
        params={
            "limit": 1,
            "before_created_at": first_page[0]["created_at"],
            "before_id": first_page[0]["id"],
        },
    ).json()["items"]
    assert len(second_page) == 1
    assert second_page[0]["id"] != first_page[0]["id"]
    terminal_page = ctx.client.get(
        "/api/v1/admin/submissions",
        params={
            "limit": 1,
            "before_created_at": second_page[0]["created_at"],
            "before_id": second_page[0]["id"],
        },
    ).json()["items"]
    assert terminal_page == []
    invalid_cursor = ctx.client.get(
        "/api/v1/admin/submissions",
        params={"before_id": first_page[0]["id"]},
    )
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "invalid_submission_cursor"
    assert ctx.client.get("/api/v1/admin/users").json()["items"]
    assert ctx.client.get("/api/v1/admin/teams").json()["items"][0]["member_count"] == 1
    protected = mutate(
        ctx.client,
        "DELETE",
        f"/api/v1/admin/challenges/{challenge['id']}",
    )
    assert protected.status_code == 409
    assert protected.json()["error"]["code"] == "challenge_has_submissions"


def test_admin_can_delete_unused_challenge(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    deleted = mutate(
        ctx.client,
        "DELETE",
        f"/api/v1/admin/challenges/{challenge['id']}",
    )
    assert deleted.status_code == 204
    assert ctx.client.get("/api/v1/admin/challenges").json()["items"] == []
