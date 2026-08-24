from __future__ import annotations

import uuid

from conftest import create_admin, login, mutate, register
from sqlalchemy import func, select
from test_challenges import create_challenge

import alpha.routes_teams as team_routes
from alpha.models import AuditEvent, Challenge, Event, Membership, OutboxEvent, Submission, Team, User
from alpha.store import RateLimitResult


def _create_team_with_members(ctx, prefix: str, member_names: tuple[str, ...]):
    owner_name = f"{prefix}-owner"
    owner = register(ctx.client, owner_name)
    assert owner.status_code == 201
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": f"{prefix} Team"})
    assert created.status_code == 201
    invite = created.json()["invite_code"]
    ids = {"owner": owner.json()["id"]}
    for name in member_names:
        mutate(ctx.client, "POST", "/api/v1/auth/logout")
        registered = register(ctx.client, f"{prefix}-{name}")
        assert registered.status_code == 201
        ids[name] = registered.json()["id"]
        joined = mutate(ctx.client, "POST", "/api/v1/teams/join", json={"invite_code": invite})
        assert joined.status_code == 200
    return created.json()["team"]["id"], ids


def test_create_join_rotate_and_leave_team(ctx):
    assert register(ctx.client, "owner").status_code == 201
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Red Team"})
    assert created.status_code == 201
    invite = created.json()["invite_code"]
    assert created.json()["team"]["role"] == "owner"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert register(ctx.client, "member").status_code == 201
    joined = mutate(ctx.client, "POST", "/api/v1/teams/join", json={"invite_code": invite})
    assert joined.status_code == 200
    assert joined.json()["team"]["name"] == "Red Team"
    assert mutate(ctx.client, "POST", "/api/v1/teams/rotate-invite").status_code == 403
    assert mutate(ctx.client, "POST", "/api/v1/teams/leave").status_code == 204

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "owner@example.com", "CorrectHorse!123").status_code == 200
    rotated = mutate(ctx.client, "POST", "/api/v1/teams/rotate-invite")
    assert rotated.status_code == 200
    assert rotated.json()["invite_code"] != invite
    assert mutate(ctx.client, "POST", "/api/v1/teams/leave").status_code == 204

    with ctx.database.session_factory() as db:
        assert db.scalar(select(Team)) is None
        assert db.scalar(select(Membership)) is None


def test_owner_cannot_leave_while_members_remain(ctx):
    register(ctx.client, "owner")
    invite = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Blue Team"}).json()["invite_code"]
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    register(ctx.client, "member")
    mutate(ctx.client, "POST", "/api/v1/teams/join", json={"invite_code": invite})
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "owner@example.com", "CorrectHorse!123")
    response = mutate(ctx.client, "POST", "/api/v1/teams/leave")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "owner_cannot_leave"


def test_team_member_cap_is_serialized_under_team_lock(ctx, monkeypatch):
    monkeypatch.setattr(team_routes, "MAX_MEMBERS_PER_TEAM", 2)
    assert register(ctx.client, "cap-owner").status_code == 201
    invite = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Capacity Team"}).json()["invite_code"]

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert register(ctx.client, "cap-member").status_code == 201
    assert mutate(ctx.client, "POST", "/api/v1/teams/join", json={"invite_code": invite}).status_code == 200

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert register(ctx.client, "cap-rejected").status_code == 201
    rejected = mutate(ctx.client, "POST", "/api/v1/teams/join", json={"invite_code": invite})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "team_capacity_reached"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Membership)) == 2
        assert (
            db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "team.joined"))
            == 1
        )


def test_team_mutation_rate_limit_is_fail_closed_and_prevents_writes(ctx, monkeypatch):
    assert register(ctx.client, "rate-owner").status_code == 201
    ctx.settings.team_mutation_rate_limit = 1
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Rate Team"})
    assert created.status_code == 201
    limited = mutate(ctx.client, "POST", "/api/v1/teams/leave")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "team_mutation_rate_limited"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Team)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action.in_(("team.created", "team.left")))
            )
            == 1
        )

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(ctx.client.app.state.store, "check_rate", unavailable)
    blocked = mutate(ctx.client, "POST", "/api/v1/teams/rotate-invite")
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "rate_limit_unavailable"
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "team.invite_rotated")
            )
            == 0
        )


def test_session_rate_denial_does_not_consume_shared_team_ip_bucket(ctx, monkeypatch):
    assert register(ctx.client, "tiered-team").status_code == 201
    calls: list[str] = []

    def deny_session(key: str, _limit: int, _window: int) -> RateLimitResult:
        calls.append(key)
        return RateLimitResult(False, 11) if ":session:" in key else RateLimitResult(True)

    monkeypatch.setattr(ctx.client.app.state.store, "check_rate", deny_session)
    rejected = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Tiered Team"})
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "11"
    assert len(calls) == 1 and ":session:" in calls[0]
    assert not any(":ip:" in key for key in calls)


def test_team_mutation_lifetime_cap_bounds_create_leave_journals(ctx, monkeypatch):
    monkeypatch.setattr(team_routes, "MAX_TEAM_MUTATIONS_PER_USER_EVENT", 2)
    assert register(ctx.client, "cycle-owner").status_code == 201
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Cycle Team"})
    assert created.status_code == 201
    assert mutate(ctx.client, "POST", "/api/v1/teams/leave").status_code == 204

    rejected = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Cycle Team Again"})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "team_mutation_limit_reached"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Team)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action.in_(("team.created", "team.left")))
            )
            == 2
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic.in_(("team.created", "team.member_left")))
            )
            == 2
        )


def test_owner_transfer_is_atomic_and_emits_audit_and_outbox(ctx):
    team_id, ids = _create_team_with_members(ctx, "transfer", ("member",))
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "transfer-owner@example.com", "CorrectHorse!123").status_code == 200

    transferred = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": ids["member"]},
    )
    assert transferred.status_code == 200
    assert transferred.json()["team"]["role"] == "member"
    roles = {item["id"]: item["role"] for item in transferred.json()["team"]["members"]}
    assert roles == {ids["owner"]: "member", ids["member"]: "owner"}
    assert mutate(ctx.client, "POST", "/api/v1/teams/rotate-invite").status_code == 403

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "transfer-member@example.com", "CorrectHorse!123").status_code == 200
    assert mutate(ctx.client, "POST", "/api/v1/teams/rotate-invite").status_code == 200

    with ctx.database.session_factory() as db:
        memberships = list(db.scalars(select(Membership).where(Membership.team_id == uuid.UUID(team_id))))
        assert {str(item.user_id): item.role for item in memberships} == roles
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "team.owner_transferred"))
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.topic == "team.owner_transferred"))
        assert audit is not None
        assert audit.actor_id == uuid.UUID(ids["owner"])
        assert audit.metadata_json["previous_owner_id"] == ids["owner"]
        assert audit.metadata_json["new_owner_id"] == ids["member"]
        assert audit.metadata_json["event_id"]
        assert outbox is not None
        assert outbox.aggregate_id == team_id
        assert outbox.payload_json["previous_owner_id"] == ids["owner"]
        assert outbox.payload_json["new_owner_id"] == ids["member"]


def test_owner_transfer_rejects_non_owner_self_outsider_and_ineligible_target(ctx):
    _team_id, ids = _create_team_with_members(ctx, "transfer-deny", ("member",))
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    outsider = register(ctx.client, "transfer-deny-outsider")
    assert outsider.status_code == 201

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "transfer-deny-member@example.com", "CorrectHorse!123").status_code == 200
    forbidden = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": ids["owner"]},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "team_owner_required"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "transfer-deny-owner@example.com", "CorrectHorse!123").status_code == 200
    self_target = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": ids["owner"]},
    )
    assert self_target.status_code == 409
    assert self_target.json()["error"]["code"] == "cannot_target_self"
    outsider_target = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": outsider.json()["id"]},
    )
    assert outsider_target.status_code == 404
    assert outsider_target.json()["error"]["code"] == "team_member_not_found"

    with ctx.database.session_factory() as db:
        member = db.get(User, uuid.UUID(ids["member"]))
        assert member is not None
        member.active = False
        db.commit()
    inactive = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": ids["member"]},
    )
    assert inactive.status_code == 409
    assert inactive.json()["error"]["code"] == "owner_target_ineligible"

    with ctx.database.session_factory() as db:
        member = db.get(User, uuid.UUID(ids["member"]))
        assert member is not None
        member.active = True
        member.role = "admin"
        db.commit()
    admin_target = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/transfer-owner",
        json={"user_id": ids["member"]},
    )
    assert admin_target.status_code == 409
    assert admin_target.json()["error"]["code"] == "owner_target_ineligible"
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "team.owner_transferred")
            )
            == 0
        )


def test_owner_can_remove_inactive_member_without_submission_activity(ctx):
    team_id, ids = _create_team_with_members(ctx, "remove", ("member",))
    with ctx.database.session_factory() as db:
        member = db.get(User, uuid.UUID(ids["member"]))
        assert member is not None
        member.active = False
        db.commit()
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "remove-owner@example.com", "CorrectHorse!123").status_code == 200

    removed = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": ids["member"]},
    )
    assert removed.status_code == 200
    assert [item["id"] for item in removed.json()["team"]["members"]] == [ids["owner"]]
    with ctx.database.session_factory() as db:
        assert db.scalar(select(Membership).where(Membership.user_id == uuid.UUID(ids["member"]))) is None
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "team.member_removed"))
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.topic == "team.member_removed"))
        assert audit is not None and audit.actor_id == uuid.UUID(ids["owner"])
        assert audit.target_id == team_id
        assert audit.metadata_json["removed_user_id"] == ids["member"]
        assert audit.metadata_json["event_id"]
        assert outbox is not None and outbox.aggregate_id == team_id
        assert outbox.payload_json["removed_user_id"] == ids["member"]


def test_remove_member_atomically_rotates_invite_and_revokes_old_code(ctx):
    owner = register(ctx.client, "invite-remove-owner")
    assert owner.status_code == 201
    created = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Invite Removal Team"})
    assert created.status_code == 201
    old_invite = created.json()["invite_code"]
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    member = register(ctx.client, "invite-remove-member")
    assert member.status_code == 201
    assert (
        mutate(
            ctx.client,
            "POST",
            "/api/v1/teams/join",
            json={"invite_code": old_invite},
        ).status_code
        == 200
    )

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "invite-remove-owner@example.com", "CorrectHorse!123").status_code == 200
    removed = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": member.json()["id"]},
    )
    assert removed.status_code == 200
    new_invite = removed.json()["invite_code"]
    assert new_invite != old_invite
    assert "invite_hash" not in removed.text

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "invite-remove-member@example.com", "CorrectHorse!123").status_code == 200
    rejected = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/join",
        json={"invite_code": old_invite},
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "invalid_invite"
    assert (
        mutate(
            ctx.client,
            "POST",
            "/api/v1/teams/join",
            json={"invite_code": new_invite},
        ).status_code
        == 200
    )
    with ctx.database.session_factory() as db:
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "team.member_removed"))
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.topic == "team.member_removed"))
        assert audit is not None and old_invite not in str(audit.metadata_json)
        assert new_invite not in str(audit.metadata_json)
        assert outbox is not None and old_invite not in str(outbox.payload_json)
        assert new_invite not in str(outbox.payload_json)


def test_remove_member_rejects_non_owner_self_and_outsider(ctx):
    _team_id, ids = _create_team_with_members(ctx, "remove-deny", ("member",))
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    outsider = register(ctx.client, "remove-deny-outsider")
    assert outsider.status_code == 201

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "remove-deny-member@example.com", "CorrectHorse!123").status_code == 200
    forbidden = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": ids["owner"]},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "team_owner_required"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "remove-deny-owner@example.com", "CorrectHorse!123").status_code == 200
    self_target = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": ids["owner"]},
    )
    assert self_target.status_code == 409
    assert self_target.json()["error"]["code"] == "cannot_target_self"
    outsider_target = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": outsider.json()["id"]},
    )
    assert outsider_target.status_code == 404
    assert outsider_target.json()["error"]["code"] == "team_member_not_found"
    malformed = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": "not-a-uuid"},
    )
    assert malformed.status_code == 422


def test_wrong_submission_blocks_member_removal_and_leave(ctx):
    team_id, ids = _create_team_with_members(ctx, "activity", ("removed", "leaver"))
    with ctx.database.session_factory() as db:
        event = db.scalar(select(Event))
        assert event is not None
        challenge = Challenge(
            event_id=event.id,
            slug="wrong-only",
            title="Wrong Only",
            category="Misc",
            connection_info=None,
            flag_type="exact",
            flag_hash="f" * 64,
        )
        db.add(challenge)
        db.flush()
        db.add_all(
            [
                Submission(
                    team_id=uuid.UUID(team_id),
                    challenge_id=challenge.id,
                    user_id=uuid.UUID(ids[name]),
                    submitted_hash=("a" if name == "removed" else "b") * 64,
                    correct=False,
                    awarded_points=0,
                    idempotency_key=f"wrong-{name}",
                    ip_hash="c" * 64,
                )
                for name in ("removed", "leaver")
            ]
        )
        db.commit()

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "activity-owner@example.com", "CorrectHorse!123").status_code == 200
    blocked_remove = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": ids["removed"]},
    )
    assert blocked_remove.status_code == 409
    assert blocked_remove.json()["error"]["code"] == "member_has_activity"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "activity-leaver@example.com", "CorrectHorse!123").status_code == 200
    blocked_leave = mutate(ctx.client, "POST", "/api/v1/teams/leave")
    assert blocked_leave.status_code == 409
    assert blocked_leave.json()["error"]["code"] == "member_has_activity"
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Membership).where(Membership.team_id == uuid.UUID(team_id))
            )
            == 3
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action.in_(("team.member_removed", "team.left")))
            )
            == 0
        )


def test_new_owner_operations_count_toward_actor_lifetime_cap(ctx, monkeypatch):
    _team_id, ids = _create_team_with_members(ctx, "cap-transfer", ("first",))
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "cap-transfer-owner@example.com", "CorrectHorse!123").status_code == 200
    monkeypatch.setattr(team_routes, "MAX_TEAM_MUTATIONS_PER_USER_EVENT", 2)
    assert (
        mutate(
            ctx.client,
            "POST",
            "/api/v1/teams/transfer-owner",
            json={"user_id": ids["first"]},
        ).status_code
        == 200
    )
    capped = mutate(ctx.client, "POST", "/api/v1/teams/leave")
    assert capped.status_code == 409
    assert capped.json()["error"]["code"] == "team_mutation_limit_reached"


def test_remove_operation_counts_toward_actor_lifetime_cap(ctx, monkeypatch):
    _team_id, ids = _create_team_with_members(ctx, "cap-remove", ("first", "second"))
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "cap-remove-owner@example.com", "CorrectHorse!123").status_code == 200
    monkeypatch.setattr(team_routes, "MAX_TEAM_MUTATIONS_PER_USER_EVENT", 2)
    assert (
        mutate(
            ctx.client,
            "POST",
            "/api/v1/teams/remove-member",
            json={"user_id": ids["first"]},
        ).status_code
        == 200
    )
    capped = mutate(
        ctx.client,
        "POST",
        "/api/v1/teams/remove-member",
        json={"user_id": ids["second"]},
    )
    assert capped.status_code == 409
    assert capped.json()["error"]["code"] == "team_mutation_limit_reached"


def test_owner_operations_close_with_registration_window(ctx):
    _team_id, ids = _create_team_with_members(ctx, "state-close", ("member",))
    with ctx.database.session_factory() as db:
        event = db.scalar(select(Event))
        assert event is not None
        event.state = "live"
        db.commit()
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert login(ctx.client, "state-close-owner@example.com", "CorrectHorse!123").status_code == 200
    for path in ("/api/v1/teams/transfer-owner", "/api/v1/teams/remove-member"):
        response = mutate(ctx.client, "POST", path, json={"user_id": ids["member"]})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "team_changes_closed"
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action.in_(("team.owner_transferred", "team.member_removed")))
            )
            == 0
        )


def test_individual_mode_creates_private_solo_team_and_rejects_team_mutations(ctx):
    create_admin(ctx)
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    switched = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"team_mode": "individual"})
    assert switched.status_code == 200
    challenge = create_challenge(ctx)
    mutate(ctx.client, "POST", "/api/v1/auth/logout")

    registered = register(ctx.client, "solo")
    assert registered.status_code == 201
    assert registered.json()["team"]["role"] == "owner"
    assert registered.json()["team"]["name"] == "solo"

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "live"})
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "solo@example.com", "CorrectHorse!123")
    for method, path, payload in [
        ("POST", "/api/v1/teams", {"name": "Forbidden"}),
        ("POST", "/api/v1/teams/join", {"invite_code": "x" * 20}),
        ("POST", "/api/v1/teams/rotate-invite", None),
        ("POST", "/api/v1/teams/transfer-owner", {"user_id": registered.json()["id"]}),
        ("POST", "/api/v1/teams/remove-member", {"user_id": registered.json()["id"]}),
        ("POST", "/api/v1/teams/leave", None),
    ]:
        response = (
            mutate(ctx.client, method, path, json=payload)
            if payload is not None
            else mutate(ctx.client, method, path)
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "individual_mode"

    solved = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "solo-solve"},
    )
    assert solved.status_code == 200 and solved.json()["correct"] is True
    assert ctx.client.get("/api/v1/teams/me").json()["team"]["name"] == "solo"
    assert ctx.client.get("/api/v1/scoreboard").json()["entries"][0]["team_name"] == "solo"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(Team.name)).startswith("solo-")

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    locked = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"team_mode": "team"})
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "team_mode_locked"
