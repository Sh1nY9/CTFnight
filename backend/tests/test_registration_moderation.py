from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import timedelta

import pytest
from conftest import create_admin, csrf_headers, login, mutate, register
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.schema import CreateSchema, DropSchema

import alpha.routes_auth as auth_routes
from alpha.config import Settings
from alpha.db import Database
from alpha.main import create_app
from alpha.models import (
    AuditEvent,
    Base,
    Event,
    OutboxEvent,
    RegistrationCode,
    SessionToken,
    User,
    utcnow,
)
from alpha.security import hash_password, hash_registration_access
from alpha.store import MemoryStore, RateLimitResult

POSTGRES_URL = os.getenv("ALPHA_TEST_POSTGRES_URL")


@pytest.fixture
def registration_postgres_app():
    if not POSTGRES_URL:
        pytest.skip("ALPHA_TEST_POSTGRES_URL is required for PostgreSQL concurrency tests")
    schema = f"alpha_registration_{uuid.uuid4().hex}"
    database = Database(POSTGRES_URL)
    administration_engine = database.engine
    with administration_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    translated_engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.engine = translated_engine
    database.session_factory.configure(bind=translated_engine)
    Base.metadata.create_all(database.engine)
    settings = Settings(
        environment="test",
        database_url=POSTGRES_URL,
        redis_url="memory://",
        secret_key="registration-concurrency-test-secret-that-is-long-enough",
        cookie_secure=False,
        allowed_origins=["http://testserver"],
        trusted_hosts=["testserver"],
        auth_rate_limit=100,
        auth_ip_rate_limit=100,
        registration_global_rate_limit=100,
    )
    app = create_app(settings=settings, database=database, store=MemoryStore())
    try:
        yield database, settings, app
    finally:
        app.state.store.close()
        database.dispose()
        with administration_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        administration_engine.dispose()


def _create_code(client: TestClient, **overrides) -> dict:
    payload = {"label": "Invitation wave"} | overrides
    response = mutate(
        client,
        "POST",
        "/api/v1/admin/registration-codes",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_code_mode(client: TestClient) -> None:
    response = mutate(
        client,
        "PUT",
        "/api/v1/admin/event",
        json={"registration_access_mode": "code"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["registration_access_mode"] == "code"


def _registration_payload(username: str, access_code: str | None = None) -> dict:
    payload = {
        "email": f"{username}@example.com",
        "username": username,
        "password": "CorrectHorse!123",
    }
    if access_code is not None:
        payload["access_code"] = access_code
    return payload


def test_registration_codes_are_hash_only_one_time_secrets_with_generic_denials(ctx, monkeypatch, caplog):
    create_admin(ctx)
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    _set_code_mode(ctx.client)
    usable = _create_code(ctx.client, label="  First   wave  ", max_uses=1)
    expired = _create_code(
        ctx.client,
        label="Expired",
        max_uses=1,
        expires_at=(utcnow() - timedelta(minutes=1)).isoformat(),
    )
    revoked = _create_code(ctx.client, label="Revoked", max_uses=1)
    assert (
        mutate(
            ctx.client,
            "DELETE",
            f"/api/v1/admin/registration-codes/{revoked['id']}",
        ).status_code
        == 204
    )

    assert usable["label"] == "First wave"
    assert usable["use_count"] == 0
    assert "token_hash" not in usable
    listing = ctx.client.get("/api/v1/admin/registration-codes")
    assert listing.status_code == 200
    assert all("access_code" not in item and "token_hash" not in item for item in listing.json()["items"])

    expected_hash = hash_registration_access(
        ctx.settings.secret_key.get_secret_value(), usable["access_code"]
    )
    with ctx.database.session_factory() as db:
        stored = db.get(RegistrationCode, uuid.UUID(usable["id"]))
        assert stored is not None
        assert stored.token_hash == expected_hash
        assert stored.token_hash != usable["access_code"]
        serialized_journal = json.dumps(
            {
                "audit": [row.metadata_json for row in db.scalars(select(AuditEvent))],
                "outbox": [row.payload_json for row in db.scalars(select(OutboxEvent))],
            },
            sort_keys=True,
        )
        assert usable["access_code"] not in serialized_journal
        assert expected_hash not in serialized_journal

    assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
    real_hash_password = auth_routes.hash_password
    password_hash_calls = 0

    def tracked_hash_password(password: str) -> str:
        nonlocal password_hash_calls
        password_hash_calls += 1
        return real_hash_password(password)

    monkeypatch.setattr(auth_routes, "hash_password", tracked_hash_password)
    denied_responses = [
        mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/register",
            json=_registration_payload("missing"),
        ),
        mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/register",
            json=_registration_payload("unknown", "not-a-real-code"),
        ),
        mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/register",
            json=_registration_payload("expired", expired["access_code"]),
        ),
        mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/register",
            json=_registration_payload("revoked", revoked["access_code"]),
        ),
    ]
    assert password_hash_calls == 0
    denial_contracts = {
        (
            response.status_code,
            response.json()["error"]["code"],
            response.json()["error"]["message"],
        )
        for response in denied_responses
    }
    assert denial_contracts == {(403, "registration_access_denied", "유효한 등록 접근 코드가 필요합니다.")}

    accepted = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/register",
        json=_registration_payload("accepted", usable["access_code"]),
    )
    assert accepted.status_code == 201, accepted.text
    assert password_hash_calls == 1
    assert usable["access_code"] not in accepted.text
    assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
    exhausted = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/register",
        json=_registration_payload("exhausted", usable["access_code"]),
    )
    assert exhausted.status_code == 403
    assert exhausted.json()["error"]["code"] == "registration_access_denied"
    assert exhausted.json()["error"]["message"] == "유효한 등록 접근 코드가 필요합니다."
    assert password_hash_calls == 1
    assert usable["access_code"] not in caplog.text
    assert expired["access_code"] not in caplog.text
    assert revoked["access_code"] not in caplog.text

    with ctx.database.session_factory() as db:
        stored = db.get(RegistrationCode, uuid.UUID(usable["id"]))
        assert stored is not None and stored.use_count == 1
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) == 1


def test_unlimited_registration_code_can_be_reused(ctx):
    create_admin(ctx)
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    _set_code_mode(ctx.client)
    code = _create_code(ctx.client, label="Unlimited")
    assert code["max_uses"] is None
    assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
    for username in ("unlimited-one", "unlimited-two"):
        response = mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/register",
            json=_registration_payload(username, code["access_code"]),
        )
        assert response.status_code == 201, response.text
        assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
    with ctx.database.session_factory() as db:
        stored = db.get(RegistrationCode, uuid.UUID(code["id"]))
        assert stored is not None and stored.max_uses is None and stored.use_count == 2


def test_registration_rate_limit_precedes_access_code_and_password_work(ctx, monkeypatch):
    create_admin(ctx)
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    _set_code_mode(ctx.client)
    assert mutate(ctx.client, "POST", "/api/v1/auth/logout").status_code == 204
    monkeypatch.setattr(
        ctx.client.app.state.store,
        "check_rate",
        lambda _key, _limit, _window: RateLimitResult(False, 9),
    )

    def forbidden_work(*_args):
        raise AssertionError("rate-limited registration performed protected work")

    monkeypatch.setattr(auth_routes, "hash_registration_access", forbidden_work)
    monkeypatch.setattr(auth_routes, "hash_password", forbidden_work)
    response = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/register",
        json=_registration_payload("rate-limited"),
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.json()["error"]["code"] == "authentication_rate_limited"


def test_archived_event_keeps_codes_read_only_and_registration_closed(ctx):
    create_admin(ctx)
    assert login(ctx.client, "admin@example.com", "AdminPassword!123").status_code == 200
    _set_code_mode(ctx.client)
    code = _create_code(ctx.client, max_uses=2)
    for state in ("live", "frozen", "ended", "archived"):
        response = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": state})
        assert response.status_code == 200, response.text

    assert ctx.client.get("/api/v1/admin/registration-codes").status_code == 200
    for response in (
        mutate(
            ctx.client,
            "POST",
            "/api/v1/admin/registration-codes",
            json={"label": "Too late"},
        ),
        mutate(
            ctx.client,
            "DELETE",
            f"/api/v1/admin/registration-codes/{code['id']}",
        ),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "event_archived"
    closed = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/register",
        json=_registration_payload("archived", code["access_code"]),
    )
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "registration_closed"


def test_participant_suspension_revokes_sessions_and_is_idempotent(ctx):
    admin = create_admin(ctx)
    assert register(ctx.client, "moderated").status_code == 201
    player_token = ctx.client.cookies.get(ctx.settings.session_cookie_name)
    with ctx.database.session_factory() as db:
        player = db.scalar(select(User).where(User.email == "moderated@example.com"))
        assert player is not None
        player_id = player.id
        assert player.credential_version == 0
        assert db.scalar(select(func.count()).select_from(SessionToken)) == 1

    admin_client = TestClient(ctx.client.app)
    try:
        assert login(admin_client, "admin@example.com", "AdminPassword!123").status_code == 200
        missing_reason = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{player_id}/status",
            json={"active": False},
        )
        assert missing_reason.status_code == 422
        controls = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{player_id}/status",
            json={"active": False, "reason": "bad\u202ereason"},
        )
        assert controls.status_code == 422

        suspended = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{player_id}/status",
            json={"active": False, "reason": "  abusive   automation  "},
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["active"] is False
        assert suspended.json()["role"] == "participant"

        ctx.client.cookies.clear()
        ctx.client.cookies.set(ctx.settings.session_cookie_name, player_token)
        denied = ctx.client.get("/api/v1/auth/me")
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "invalid_session"

        repeated = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{player_id}/status",
            json={"active": False, "reason": "same state"},
        )
        assert repeated.status_code == 200

        with ctx.database.session_factory() as db:
            player = db.get(User, player_id)
            assert player is not None
            assert player.active is False
            assert player.credential_version == 1
            assert (
                db.scalar(
                    select(func.count()).select_from(SessionToken).where(SessionToken.user_id == player_id)
                )
                == 0
            )
            audits = list(
                db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "user.status_changed",
                        AuditEvent.target_id == str(player_id),
                    )
                )
            )
            outbox = list(
                db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.topic == "user.status_changed",
                        OutboxEvent.aggregate_id == str(player_id),
                    )
                )
            )
            assert len(audits) == 1
            assert audits[0].metadata_json["reason"] == "abusive automation"
            assert len(outbox) == 1

        reactivated = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{player_id}/status",
            json={"active": True, "reason": "appeal accepted"},
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["active"] is True
        assert login(ctx.client, "moderated@example.com", "CorrectHorse!123").status_code == 200

        admin_target = mutate(
            admin_client,
            "PUT",
            f"/api/v1/admin/users/{admin.id}/status",
            json={"active": False, "reason": "must not work"},
        )
        assert admin_target.status_code == 409
        assert admin_target.json()["error"]["code"] == "participant_required"
    finally:
        admin_client.close()


def test_postgresql_single_use_registration_code_is_consumed_once(registration_postgres_app, monkeypatch):
    database, settings, app = registration_postgres_app
    access_code = "single-use-registration-code"
    with database.session_factory() as db:
        admin = User(
            email="code-admin@example.com",
            username="code-admin",
            password_hash="unused",
            role="admin",
        )
        db.add(admin)
        db.flush()
        event = Event(
            name="Code race",
            slug="code-race",
            state="registration",
            registration_at=utcnow(),
            registration_access_mode="code",
        )
        db.add(event)
        db.flush()
        code = RegistrationCode(
            event_id=event.id,
            token_hash=hash_registration_access(settings.secret_key.get_secret_value(), access_code),
            label="One seat",
            max_uses=1,
            created_by=admin.id,
        )
        db.add(code)
        db.commit()
        code_id = code.id

    real_hash_password = auth_routes.hash_password
    barrier = threading.Barrier(2)

    def synchronized_hash_password(password: str) -> str:
        barrier.wait(timeout=10)
        return real_hash_password(password)

    monkeypatch.setattr(auth_routes, "hash_password", synchronized_hash_password)
    clients = [TestClient(app), TestClient(app)]
    tokens = [csrf_headers(client)["X-CSRF-Token"] for client in clients]
    responses: list = []

    def register_candidate(index: int) -> None:
        responses.append(
            clients[index].post(
                "/api/v1/auth/register",
                headers={"X-CSRF-Token": tokens[index]},
                json=_registration_payload(f"racer-{index}", access_code),
            )
        )

    threads = [threading.Thread(target=register_candidate, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [201, 403]
    denied = next(response for response in responses if response.status_code == 403)
    assert denied.json()["error"]["code"] == "registration_access_denied"
    with database.session_factory() as db:
        stored = db.get(RegistrationCode, code_id)
        assert stored is not None and stored.use_count == 1
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) == 1
    for client in clients:
        client.close()


def test_postgresql_suspension_wins_against_inflight_password_change(registration_postgres_app, monkeypatch):
    database, _settings, app = registration_postgres_app
    old_password = "OriginalPassword!123"
    with database.session_factory() as db:
        admin = User(
            email="moderation-admin@example.com",
            username="moderation-admin",
            password_hash=hash_password("AdminPassword!123"),
            role="admin",
        )
        event = Event(
            name="Moderation race",
            slug="moderation-race",
            state="registration",
            registration_at=utcnow(),
        )
        db.add_all([admin, event])
        db.commit()

    victim = TestClient(app)
    moderator = TestClient(app)
    created = victim.post(
        "/api/v1/auth/register",
        headers=csrf_headers(victim),
        json=_registration_payload("race-player") | {"password": old_password},
    )
    assert created.status_code == 201, created.text
    assert login(moderator, "moderation-admin@example.com", "AdminPassword!123").status_code == 200
    victim_csrf = csrf_headers(victim)["X-CSRF-Token"]
    moderator_csrf = csrf_headers(moderator)["X-CSRF-Token"]
    with database.session_factory() as db:
        player_id = db.scalar(select(User.id).where(User.email == "race-player@example.com"))
        assert player_id is not None

    real_verify_password = auth_routes.verify_password
    password_verified = threading.Event()
    resume_password_change = threading.Event()

    def controlled_verify(password_hash: str, password: str) -> bool:
        result = real_verify_password(password_hash, password)
        if password == old_password and result:
            password_verified.set()
            assert resume_password_change.wait(timeout=10)
        return result

    monkeypatch.setattr(auth_routes, "verify_password", controlled_verify)
    password_responses: list = []

    def change_password() -> None:
        password_responses.append(
            victim.post(
                "/api/v1/auth/change-password",
                headers={"X-CSRF-Token": victim_csrf},
                json={
                    "current_password": old_password,
                    "new_password": "ChangedPassword!456",
                },
            )
        )

    thread = threading.Thread(target=change_password)
    thread.start()
    assert password_verified.wait(timeout=10)
    try:
        suspended = moderator.put(
            f"/api/v1/admin/users/{player_id}/status",
            headers={"X-CSRF-Token": moderator_csrf},
            json={"active": False, "reason": "security response"},
        )
        assert suspended.status_code == 200, suspended.text
    finally:
        resume_password_change.set()
        thread.join(timeout=20)
    assert not thread.is_alive()
    assert len(password_responses) == 1
    assert password_responses[0].status_code == 401
    assert password_responses[0].json()["error"]["code"] == "invalid_session"
    with database.session_factory() as db:
        player = db.get(User, player_id)
        assert player is not None and player.active is False and player.credential_version == 1
        assert (
            db.scalar(select(func.count()).select_from(SessionToken).where(SessionToken.user_id == player_id))
            == 0
        )
    victim.close()
    moderator.close()
