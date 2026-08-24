from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import create_admin, login, mutate, production_settings_values, register
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from test_challenges import activate_for_player, admin_login, create_challenge, prepare_player, set_live

import alpha.routes_participant as participant_routes
from alpha.config import Settings
from alpha.db import Database
from alpha.main import create_app
from alpha.models import Base, Challenge, Event, Solve, Submission, Team, User
from alpha.services import current_event_query
from alpha.store import MemoryStore


def _challenge_payload(**overrides):
    payload = {
        "slug": "audit-challenge",
        "title": "Audit Challenge",
        "category": "Misc",
        "description_md": "Audit fixture.",
        "scoring_type": "fixed",
        "initial_points": 100,
        "minimum_points": 100,
        "decay": 20,
        "visible": True,
        "max_attempts": 0,
        "prerequisite_ids": [],
        "flag": {"type": "exact", "value": "FLAG{audit}"},
    }
    payload.update(overrides)
    return payload


def _assert_archived(response):
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "event_archived"


def test_frozen_scoring_and_freeze_cutoff_are_immutable(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    solved = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "before-score-lock"},
    )
    assert solved.status_code == 200

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    assert mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "frozen"}).status_code == 200
    before = ctx.client.get("/api/v1/scoreboard").json()["entries"]

    scoring = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/challenges/{challenge['id']}",
        json={"initial_points": 500, "minimum_points": 500},
    )
    assert scoring.status_code == 409
    assert scoring.json()["error"]["code"] == "frozen_scoring_locked"

    cutoff = mutate(
        ctx.client,
        "PUT",
        "/api/v1/admin/event",
        json={"freeze_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
    )
    assert cutoff.status_code == 409
    assert cutoff.json()["error"]["code"] == "scoreboard_freeze_locked"

    title_only = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/challenges/{challenge['id']}",
        json={"title": "Renamed Without Score Change"},
    )
    assert title_only.status_code == 200
    assert ctx.client.get("/api/v1/scoreboard").json()["entries"] == before


@pytest.mark.parametrize("offset_seconds", [3600, -3600])
def test_entering_frozen_clamps_future_cutoff_and_preserves_past_cutoff(ctx, offset_seconds):
    admin_login(ctx)
    scheduled = datetime.now(UTC) + timedelta(seconds=offset_seconds)
    live = mutate(
        ctx.client,
        "PUT",
        "/api/v1/admin/event",
        json={"state": "live", "freeze_at": scheduled.isoformat()},
    )
    assert live.status_code == 200
    before = datetime.now(UTC)
    frozen = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "frozen"})
    after = datetime.now(UTC)
    actual = datetime.fromisoformat(frozen.json()["freeze_at"])
    if offset_seconds > 0:
        assert before <= actual <= after
    else:
        assert abs((actual - scheduled).total_seconds()) < 0.01


def test_past_freeze_time_is_still_editable_before_live(ctx):
    admin_login(ctx)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    first = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"freeze_at": past})
    assert first.status_code == 200
    corrected = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"freeze_at": future})
    assert corrected.status_code == 200
    actual = datetime.fromisoformat(corrected.json()["freeze_at"])
    assert abs((actual - datetime.fromisoformat(future)).total_seconds()) < 0.01


def test_archived_event_blocks_all_admin_content_mutations_but_not_reads(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    announcement = mutate(
        ctx.client,
        "POST",
        "/api/v1/admin/announcements",
        json={"title": "Before archive", "body_md": "Preserve this."},
    ).json()
    for state in ("live", "frozen", "ended", "archived"):
        response = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": state})
        assert response.status_code == 200, response.text

    _assert_archived(mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"name": "Changed"}))
    _assert_archived(mutate(ctx.client, "POST", "/api/v1/admin/challenges", json=_challenge_payload()))
    _assert_archived(
        mutate(
            ctx.client,
            "PUT",
            f"/api/v1/admin/challenges/{challenge['id']}",
            json={"title": "Changed"},
        )
    )
    _assert_archived(
        mutate(
            ctx.client,
            "POST",
            f"/api/v1/admin/challenges/{challenge['id']}/visibility",
            json={"visible": False},
        )
    )
    _assert_archived(mutate(ctx.client, "DELETE", f"/api/v1/admin/challenges/{challenge['id']}"))
    _assert_archived(
        mutate(
            ctx.client,
            "POST",
            "/api/v1/admin/announcements",
            json={"title": "Changed", "body_md": "No"},
        )
    )
    _assert_archived(
        mutate(
            ctx.client,
            "PUT",
            f"/api/v1/admin/announcements/{announcement['id']}",
            json={"title": "Changed"},
        )
    )
    _assert_archived(mutate(ctx.client, "DELETE", f"/api/v1/admin/announcements/{announcement['id']}"))

    assert ctx.client.get("/api/v1/admin/event").status_code == 200
    assert ctx.client.get("/api/v1/admin/challenges").status_code == 200
    assert ctx.client.get("/api/v1/admin/announcements").status_code == 200


def test_event_state_transition_must_be_same_or_exactly_next(ctx):
    admin_login(ctx)
    slug = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"slug": "changed-slug"})
    assert slug.status_code == 409
    assert slug.json()["error"]["code"] == "event_slug_locked"
    skipped = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "frozen"})
    assert skipped.status_code == 409
    assert skipped.json()["error"]["code"] == "invalid_state_transition"
    assert mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "registration"}).status_code == 200
    assert mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "live"}).status_code == 200
    skipped_again = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "ended"})
    assert skipped_again.status_code == 409
    assert skipped_again.json()["error"]["code"] == "invalid_state_transition"


def test_registration_and_team_changes_are_registration_only(ctx):
    create_admin(ctx)
    assert register(ctx.client, "owner").status_code == 201
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Locked Team"})
    invite = created.json()["invite_code"]
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert register(ctx.client, "waiting").status_code == 201
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    set_live(ctx)

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "owner@example.com", "CorrectHorse!123").status_code == 200
    for path in ("/api/v1/teams/rotate-invite", "/api/v1/teams/leave"):
        response = mutate(ctx.client, "POST", path)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "team_changes_closed"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "waiting@example.com", "CorrectHorse!123").status_code == 200
    for path, payload in (
        ("/api/v1/teams", {"name": "Late Team"}),
        ("/api/v1/teams/join", {"invite_code": invite}),
    ):
        response = mutate(ctx.client, "POST", path, json=payload)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "team_changes_closed"
    late = register(ctx.client, "late")
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "registration_closed"


def test_submit_rechecks_event_gate_before_commit(ctx, monkeypatch):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    calls = 0

    def closes_before_commit(_event):
        nonlocal calls
        calls += 1
        return calls == 1

    monkeypatch.setattr(participant_routes, "state_allows_submission", closes_before_commit)
    response = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "gate-closes"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "submissions_closed"
    assert calls == 2
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


def test_current_event_lock_compiles_to_postgresql_for_update():
    exclusive_sql = str(current_event_query(for_update=True).compile(dialect=postgresql.dialect()))
    shared_sql = str(current_event_query(for_share=True).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in exclusive_sql
    assert "FOR SHARE" in shared_sql


def test_flag_definitions_respect_runtime_limit_on_create_and_update(ctx):
    admin_login(ctx)
    ctx.settings.max_flag_length = 16
    too_long = mutate(
        ctx.client,
        "POST",
        "/api/v1/admin/challenges",
        json=_challenge_payload(flag={"type": "exact", "value": "X" * 17}),
    )
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "flag_too_long"

    challenge = create_challenge(ctx, slug="valid-flag", flag={"type": "exact", "value": "A" * 16})
    regex = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/challenges/{challenge['id']}",
        json={"flag": {"type": "regex", "value": "^" + "A" * 15 + "$"}},
    )
    assert regex.status_code == 422
    assert regex.json()["error"]["code"] == "flag_too_long"
    with ctx.database.session_factory() as db:
        assert db.get(Challenge, uuid.UUID(challenge["id"])).flag_type == "exact"


def test_challenges_are_hidden_until_live_start_but_admin_inventory_remains_visible(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    assert ctx.client.get("/api/v1/challenges").json() == {"items": []}
    assert ctx.client.get(f"/api/v1/challenges/{challenge['id']}").status_code == 404

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    assert len(ctx.client.get("/api/v1/admin/challenges").json()["items"]) == 1
    future_start = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    opened_early = mutate(
        ctx.client,
        "PUT",
        "/api/v1/admin/event",
        json={"state": "live", "start_at": future_start},
    )
    assert opened_early.status_code == 200
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "player@example.com", "CorrectHorse!123").status_code == 200
    assert ctx.client.get("/api/v1/challenges").json() == {"items": []}
    assert ctx.client.get(f"/api/v1/challenges/{challenge['id']}").status_code == 404

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    assert mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"start_at": None}).status_code == 200
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "player@example.com", "CorrectHorse!123")
    assert [item["slug"] for item in ctx.client.get("/api/v1/challenges").json()["items"]] == ["welcome"]


def test_scheduled_freeze_uses_a_distinct_cache_phase(ctx):
    admin_login(ctx)
    challenge = create_challenge(ctx)
    prepare_player(ctx)
    activate_for_player(ctx)
    assert (
        mutate(
            ctx.client,
            "POST",
            f"/api/v1/challenges/{challenge['id']}/submit",
            json={"flag": "FLAG{welcome}", "idempotency_key": "scheduled-cache"},
        ).status_code
        == 200
    )
    live_board = ctx.client.get("/api/v1/scoreboard").json()
    assert live_board["frozen"] is False and len(live_board["entries"]) == 1

    with ctx.database.session_factory() as db:
        event = db.scalar(select(Event))
        solve = db.scalar(select(Solve))
        event.freeze_at = solve.solved_at - timedelta(seconds=1)
        db.commit()

    frozen_board = ctx.client.get("/api/v1/scoreboard").json()
    assert frozen_board["frozen"] is True
    assert frozen_board["entries"] == []


def test_team_name_is_normalized_before_length_validation(ctx):
    assert register(ctx.client, "owner").status_code == 201
    short = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "   a   "})
    assert short.status_code == 422
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "  Red   Team  "})
    assert created.status_code == 201
    assert created.json()["team"]["name"] == "Red Team"


def test_database_enforces_case_insensitive_account_and_team_uniqueness(ctx):
    with ctx.database.session_factory() as db:
        owner = User(email="Case@Example.com", username="CaseUser", password_hash="hash")
        db.add(owner)
        db.commit()
        db.refresh(owner)

        db.add(User(email="case@example.com", username="different", password_hash="hash"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(User(email="different@example.com", username="caseuser", password_hash="hash"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(Team(name="Red Team", invite_hash="a" * 64, creator_id=owner.id))
        db.commit()
        db.add(Team(name="red team", invite_hash="b" * 64, creator_id=owner.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_production_disables_external_asset_api_docs(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'docs.db'}")
    Base.metadata.create_all(database.engine)
    settings = Settings(
        **production_settings_values(
            tmp_path,
            allowed_origins=["https://ctf.example"],
            trusted_hosts=["testserver"],
        )
    )
    app = create_app(settings=settings, database=database, store=MemoryStore())
    with TestClient(app) as client:
        for path in ("/api/docs", "/api/redoc", "/api/openapi.json", "/redoc"):
            assert client.get(path).status_code == 404
