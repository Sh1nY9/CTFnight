from __future__ import annotations

from datetime import timedelta

from conftest import create_admin, csrf_headers, login, mutate, register
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

import alpha.routes_auth as auth_routes
from alpha.models import AuditEvent, OutboxEvent, SessionToken, User, utcnow
from alpha.routes_auth import expired_session_cleanup_statement
from alpha.security import hash_session, verify_password
from alpha.store import MemoryStore, RateLimitResult


def test_health_meta_and_structured_error(ctx):
    assert ctx.client.get("/api/v1/health/live").json() == {"status": "ok"}
    assert ctx.client.get("/api/v1/health/ready").json() == {"status": "ok"}
    meta = ctx.client.get("/api/v1/meta").json()
    assert meta["name"] == "CTFnight"
    assert meta["session_cookie"] == "alpha_session"
    assert meta["csrf_cookie"] == "alpha_csrf"
    assert meta["limits"]["max_flag_length"] == ctx.settings.max_flag_length
    assert meta["limits"]["max_submissions_per_team_challenge"] == 1000
    assert meta["limits"]["max_submissions_per_team_event"] == 10_000
    assert meta["limits"]["max_members_per_team"] == 100
    assert meta["limits"]["max_participant_users"] == 100_000
    assert meta["limits"]["max_active_sessions_per_user"] == 10
    assert ctx.client.get("/api/openapi.json").json()["info"]["title"] == "CTFnight API"

    denied = ctx.client.post(
        "/api/v1/auth/register",
        json={"email": "one@example.com", "username": "one", "password": "CorrectHorse!123"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "csrf_failed"
    assert denied.json()["error"]["request_id"]
    assert denied.headers["X-Request-ID"]


def test_csrf_token_is_signed_and_cookie_must_match(ctx):
    token = csrf_headers(ctx.client)["X-CSRF-Token"]
    assert ctx.client.cookies.get("alpha_csrf") == token
    response = ctx.client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": token + "tampered"},
        json={"email": "one@example.com", "username": "one", "password": "CorrectHorse!123"},
    )
    assert response.status_code == 403


def test_register_login_logout_and_hash_only_storage(ctx):
    response = register(ctx.client, "player")
    assert response.status_code == 201
    assert response.json()["username"] == "player"
    assert response.json()["team"] is None
    assert ctx.client.cookies.get("alpha_session")
    assert ctx.client.get("/api/v1/auth/me").status_code == 200

    with ctx.database.session_factory() as db:
        user = db.scalar(select(User).where(User.email == "player@example.com"))
        assert user is not None
        assert user.password_hash != "CorrectHorse!123"
        assert verify_password(user.password_hash, "CorrectHorse!123")
        session = db.scalar(select(SessionToken).where(SessionToken.user_id == user.id))
        assert session is not None
        assert session.token_hash != ctx.client.cookies.get("alpha_session")

    logged_out = mutate(ctx.client, "POST", "/api/v1/auth/logout")
    assert logged_out.status_code == 204
    assert ctx.client.get("/api/v1/auth/me").status_code == 401
    assert login(ctx.client, "player@example.com", "wrong").status_code == 401
    assert login(ctx.client, "player@example.com", "CorrectHorse!123").status_code == 200


def test_repetitive_login_logout_audit_is_coalesced_per_user_and_action(ctx):
    assert register(ctx.client, "coalesced").status_code == 201
    for _index in range(3):
        assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
        assert login(ctx.client, "coalesced@example.com", "CorrectHorse!123").status_code == 200

    with ctx.database.session_factory() as db:
        rows = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.in_(("auth.login", "auth.logout")))
                .order_by(AuditEvent.action)
            )
        )
        assert len(rows) == 2
        assert {row.action: row.metadata_json["occurrences"] for row in rows} == {
            "auth.login": 3,
            "auth.logout": 3,
        }


def test_password_change_audit_and_pending_outbox_are_coalesced(ctx):
    assert register(ctx.client, "password-events").status_code == 201
    first = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "CorrectHorse!123", "new_password": "MiddlePassword!456"},
    )
    assert first.status_code == 200
    second = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "MiddlePassword!456", "new_password": "FinalPassword!789"},
    )
    assert second.status_code == 200

    with ctx.database.session_factory() as db:
        audits = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.password_changed")))
        outbox = list(db.scalars(select(OutboxEvent).where(OutboxEvent.topic == "user.password_changed")))
        assert len(audits) == 1 and audits[0].metadata_json["occurrences"] == 2
        assert len(outbox) == 1 and outbox[0].payload_json["occurrences"] == 2


def test_participant_capacity_rejects_n_plus_one_without_rows(ctx, monkeypatch):
    monkeypatch.setattr(auth_routes, "MAX_PARTICIPANT_USERS", 1)
    assert register(ctx.client, "capacity-one").status_code == 201
    rejected = register(ctx.client, "capacity-two")
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "participant_capacity_reached"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) == 1
        assert db.scalar(select(func.count()).select_from(SessionToken)) == 1
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.register")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "user.registered")
            )
            == 1
        )


def test_global_registration_budget_cannot_be_bypassed_with_unique_identity(ctx):
    ctx.settings.registration_global_rate_limit = 1
    assert register(ctx.client, "budget-one").status_code == 201
    rejected = register(ctx.client, "budget-two")
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "authentication_rate_limited"
    with ctx.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) == 1


def test_local_auth_rate_denial_does_not_consume_shared_registration_budget(ctx, monkeypatch):
    calls: list[str] = []

    def deny_ip(key: str, _limit: int, _window: int) -> RateLimitResult:
        calls.append(key)
        return RateLimitResult(False, 7) if ":ip:" in key else RateLimitResult(True)

    monkeypatch.setattr(ctx.client.app.state.store, "check_rate", deny_ip)
    rejected = register(ctx.client, "tiered-auth")
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "7"
    assert len(calls) == 2
    assert ":identity:" in calls[0]
    assert ":ip:" in calls[1]
    assert "auth:register:global" not in calls


def test_active_session_cap_revokes_oldest_session(ctx, monkeypatch):
    monkeypatch.setattr(auth_routes, "MAX_ACTIVE_SESSIONS_PER_USER", 2)
    assert register(ctx.client, "session-cap").status_code == 201
    oldest_token = ctx.client.cookies.get("alpha_session")
    assert login(ctx.client, "session-cap@example.com", "CorrectHorse!123").status_code == 200
    assert login(ctx.client, "session-cap@example.com", "CorrectHorse!123").status_code == 200

    oldest_hash = hash_session(ctx.settings.secret_key.get_secret_value(), oldest_token)
    with ctx.database.session_factory() as db:
        user = db.scalar(select(User).where(User.email == "session-cap@example.com"))
        assert (
            db.scalar(select(func.count()).select_from(SessionToken).where(SessionToken.user_id == user.id))
            == 2
        )
        assert db.scalar(select(SessionToken.id).where(SessionToken.token_hash == oldest_hash)) is None


def test_password_change_uses_stable_user_rate_key_after_session_rotation(ctx):
    assert register(ctx.client, "stable-password-rate").status_code == 201
    ctx.settings.auth_rate_limit = 1
    first = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "CorrectHorse!123", "new_password": "RotatedPassword!456"},
    )
    assert first.status_code == 200
    second = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "RotatedPassword!456", "new_password": "BypassAttempt!789"},
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "authentication_rate_limited"
    with ctx.database.session_factory() as db:
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.password_changed"))
        assert audit is not None and audit.metadata_json["occurrences"] == 1


def test_new_sessions_clean_expired_rows_in_bounded_indexed_batches(ctx):
    admin = create_admin(ctx)
    ctx.settings.session_cleanup_batch_size = 2
    now = utcnow()
    with ctx.database.session_factory() as db:
        for index in range(3):
            db.add(
                SessionToken(
                    token_hash=f"{index + 1:064x}",
                    user_id=admin.id,
                    expires_at=now - timedelta(hours=index + 1),
                )
            )
        db.add(
            SessionToken(
                token_hash="f" * 64,
                user_id=admin.id,
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()

    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(SessionToken).where(SessionToken.expires_at <= utcnow())
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(SessionToken)) == 3

    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(SessionToken).where(SessionToken.expires_at <= utcnow())
            )
            == 0
        )


def test_expired_session_cleanup_uses_bounded_postgresql_skip_locked_query():
    sql = str(expired_session_cleanup_statement(utcnow(), 100).compile(dialect=postgresql.dialect()))
    assert "sessions.expires_at <=" in sql
    assert "LIMIT" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert any(index.name == "ix_sessions_expires_at" for index in SessionToken.__table__.indexes)


def test_password_change_revokes_other_sessions_and_clears_bootstrap_flag(ctx):
    admin = create_admin(ctx)
    with ctx.database.session_factory() as db:
        stored = db.get(User, admin.id)
        stored.password_change_required = True
        db.commit()
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    old_token = ctx.client.cookies.get("alpha_session")

    response = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "AdminPassword!123", "new_password": "NewAdminPassword!456"},
    )
    assert response.status_code == 200
    assert response.json()["password_change_required"] is False
    assert ctx.client.cookies.get("alpha_session") != old_token
    with ctx.database.session_factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(SessionToken).where(SessionToken.user_id == admin.id))
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "auth.password_changed")
            )
            == 1
        )
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 401


def test_bootstrap_password_change_is_required_before_admin_api(ctx):
    admin = create_admin(ctx)
    with ctx.database.session_factory() as db:
        stored = db.get(User, admin.id)
        stored.password_change_required = True
        db.commit()
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    denied = ctx.client.get("/api/v1/admin/event")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "password_change_required"
    changed = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "AdminPassword!123", "new_password": "ChangedAdmin!456"},
    )
    assert changed.status_code == 200
    assert ctx.client.get("/api/v1/admin/event").status_code == 200


def test_admin_permission_is_enforced(ctx):
    assert register(ctx.client, "player").status_code == 201
    response = ctx.client.get("/api/v1/admin/event")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


def test_login_rate_limit_is_enforced(ctx):
    ctx.settings.auth_rate_limit = 1
    first = login(ctx.client, "missing@example.com", "wrong")
    assert first.status_code == 401
    second = login(ctx.client, "missing@example.com", "wrong")
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "authentication_rate_limited"


def test_readiness_failure_uses_structured_error(ctx):
    class BrokenStore(MemoryStore):
        def ping(self) -> bool:
            raise RuntimeError("unavailable")

    original = ctx.client.app.state.store
    ctx.client.app.state.store = BrokenStore()
    try:
        response = ctx.client.get("/api/v1/health/ready")
    finally:
        ctx.client.app.state.store = original
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
    assert response.json()["error"]["checks"] == {"database": True, "redis": False}
