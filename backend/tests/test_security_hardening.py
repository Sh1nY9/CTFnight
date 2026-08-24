from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import csrf_headers, mutate, production_settings_values, register
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from alpha.config import Settings
from alpha.main import create_app
from alpha.models import SessionToken, User
from alpha.store import MemoryStore


def test_change_password_rate_limit_precedes_argon_and_fails_closed(ctx, monkeypatch):
    assert register(ctx.client, "player").status_code == 201
    ctx.settings.auth_rate_limit = 1

    from alpha import routes_auth

    real_verify = routes_auth.verify_password
    calls = 0

    def tracked_verify(password_hash: str, password: str) -> bool:
        nonlocal calls
        calls += 1
        return real_verify(password_hash, password)

    monkeypatch.setattr(routes_auth, "verify_password", tracked_verify)
    first = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "NewPassword!456"},
    )
    second = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "NewPassword!456"},
    )
    assert first.status_code == 401
    assert second.status_code == 429
    assert calls == 1

    class BrokenStore(MemoryStore):
        def check_rate(self, key: str, limit: int, window_seconds: int):
            raise RuntimeError("unavailable")

    original_store = ctx.client.app.state.store
    ctx.client.app.state.store = BrokenStore()
    try:
        unavailable = mutate(
            ctx.client,
            "POST",
            "/api/v1/auth/change-password",
            json={"current_password": "wrong", "new_password": "NewPassword!456"},
        )
    finally:
        ctx.client.app.state.store = original_store
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "rate_limit_unavailable"
    assert calls == 1


def test_session_credential_generation_invalidates_rows_even_before_cleanup(ctx):
    assert register(ctx.client, "player").status_code == 201
    with ctx.database.session_factory() as db:
        user = db.scalar(select(User).where(User.email == "player@example.com"))
        assert user is not None
        user.credential_version += 1
        db.commit()
        assert db.scalar(select(func.count()).select_from(SessionToken)) == 1

    denied = ctx.client.get("/api/v1/auth/me")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_session"


def test_csrf_is_httponly_rotated_and_bound_to_the_active_session(ctx):
    anonymous_token = csrf_headers(ctx.client)["X-CSRF-Token"]
    created_a = ctx.client.post(
        "/api/v1/auth/register",
        headers={"X-CSRF-Token": anonymous_token},
        json={
            "email": "one@example.com",
            "username": "one",
            "password": "CorrectHorse!123",
        },
    )
    assert created_a.status_code == 201
    session_a = ctx.client.cookies.get("alpha_session")
    token_a = created_a.headers["X-CSRF-Token"]
    assert token_a != anonymous_token
    assert any(
        item.startswith("alpha_csrf=") and "HttpOnly" in item
        for item in created_a.headers.get_list("set-cookie")
    )

    ctx.client.cookies.clear()
    assert register(ctx.client, "two", "two@example.com").status_code == 201
    session_b = ctx.client.cookies.get("alpha_session")
    assert session_a and session_b and session_a != session_b

    ctx.client.cookies.delete("alpha_csrf")
    ctx.client.cookies.set("alpha_csrf", token_a)
    denied = ctx.client.post(
        "/api/v1/teams",
        headers={"X-CSRF-Token": token_a},
        json={"name": "Should Not Exist"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "csrf_failed"


def test_request_body_limit_rejects_content_length_and_chunked_before_endpoint(ctx, monkeypatch):
    settings = ctx.settings.model_copy(update={"max_request_body_bytes": 1024})
    app = create_app(settings=settings, database=ctx.database, store=MemoryStore())
    calls = 0

    def should_not_verify(password_hash: str, password: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr("alpha.routes_auth.verify_password", should_not_verify)
    with TestClient(app) as client:
        token = csrf_headers(client)["X-CSRF-Token"]
        oversized = client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": token},
            json={
                "email": "missing@example.com",
                "password": "wrong",
                "padding": "x" * 2000,
            },
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "request_too_large"
        assert oversized.headers["Cache-Control"] == "no-store, private"

        chunks: Iterator[bytes] = iter(
            [
                b'{"email":"missing@example.com","password":"',
                b"x" * 1500,
                b'"}',
            ]
        )
        chunked = client.post(
            "/api/v1/auth/login",
            headers={
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
                "X-CSRF-Token": token,
            },
            content=chunks,
        )
        assert chunked.status_code == 413
        assert chunked.json()["error"]["code"] == "request_too_large"
    assert calls == 0


def test_invalid_host_and_csrf_reject_without_consuming_request_body(ctx):
    consumed = 0

    def body():
        nonlocal consumed
        consumed += 1
        yield b'{"name":"must-not-be-read"}'

    csrf_denied = ctx.client.post(
        "/api/v1/teams",
        headers={"Content-Type": "application/json", "Origin": "http://testserver"},
        content=body(),
    )
    assert csrf_denied.status_code == 403
    assert csrf_denied.headers["Access-Control-Allow-Origin"] == "http://testserver"
    assert consumed == 0

    host_denied = ctx.client.post(
        "/api/v1/teams",
        headers={
            "Content-Type": "application/json",
            "Host": "untrusted.example",
            "Origin": "http://testserver",
        },
        content=body(),
    )
    assert host_denied.status_code == 400
    assert host_denied.headers["Access-Control-Allow-Origin"] == "http://testserver"
    assert consumed == 0


def test_successful_login_runs_argon_verification_once_without_a_password_race(ctx, monkeypatch):
    assert register(ctx.client, "player").status_code == 201
    mutate(ctx.client, "POST", "/api/v1/auth/logout")

    from alpha import routes_auth

    real_verify = routes_auth.verify_password
    calls = 0

    def tracked_verify(password_hash: str, password: str) -> bool:
        nonlocal calls
        calls += 1
        return real_verify(password_hash, password)

    monkeypatch.setattr(routes_auth, "verify_password", tracked_verify)
    logged_in = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "player@example.com", "password": "CorrectHorse!123"},
    )
    assert logged_in.status_code == 200
    assert calls == 1


def test_team_name_rejects_control_format_and_bidi_characters(ctx):
    assert register(ctx.client, "player").status_code == 201
    for name in ("Red\nTeam", "Red\u200bTeam", "Red\u202eTeam"):
        response = mutate(ctx.client, "POST", "/api/v1/teams", json={"name": name})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_api_security_headers_are_applied_to_success_and_error(ctx):
    success = ctx.client.get("/api/v1/health/live")
    error = ctx.client.post("/api/v1/auth/login", json={})
    for response in (success, error):
        assert response.headers["Cache-Control"] == "no-store, private"
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"


def test_database_lock_timeout_is_a_bounded_retryable_response(ctx):
    def locked() -> None:
        raise OperationalError("redacted", {}, RuntimeError("lock timeout"))

    ctx.client.app.add_api_route("/api/v1/test-database-lock", locked, methods=["GET"])
    response = ctx.client.get("/api/v1/test-database-lock")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "database_temporarily_unavailable"
    assert response.headers["Cache-Control"] == "no-store, private"


def test_argon_saturation_fails_fast_and_returns_retryable_response(ctx, monkeypatch):
    from alpha import routes_auth, security

    assert security._argon2_slots.acquire(blocking=False)
    assert security._argon2_slots.acquire(blocking=False)
    try:
        with pytest.raises(security.PasswordWorkBusy):
            security.hash_password("MustFailFast!123")
    finally:
        security._argon2_slots.release()
        security._argon2_slots.release()

    def saturated(_password_hash: str, _password: str) -> bool:
        raise security.PasswordWorkBusy

    monkeypatch.setattr(routes_auth, "verify_password", saturated)
    response = mutate(
        ctx.client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["error"]["code"] == "password_service_busy"


def test_production_rejects_insecure_cookie_override_and_uses_host_prefix(ctx, tmp_path):
    values = production_settings_values(tmp_path)
    with pytest.raises(ValidationError):
        Settings(**(values | {"cookie_secure": False}))

    settings = Settings(**values)
    app = create_app(settings=settings, database=ctx.database, store=MemoryStore())
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/auth/csrf")
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        host_cookies = [item for item in cookies if item.startswith("__Host-alpha_")]
        assert len(host_cookies) == 2
        assert all("Secure" in item and "HttpOnly" in item and "Path=/" in item for item in host_cookies)
        assert all("Domain=" not in item for item in host_cookies)
        meta = client.get("/api/v1/meta").json()
        assert meta["session_cookie"] == "__Host-alpha_session"
        assert meta["csrf_cookie"] == "__Host-alpha_csrf"
        created = client.post(
            "/api/v1/auth/register",
            headers={"X-CSRF-Token": response.json()["csrf_token"]},
            json={
                "email": "production@example.com",
                "username": "production",
                "password": "ProductionPassword!123",
            },
        )
        assert created.status_code == 201
        session_cookies = [
            item
            for item in created.headers.get_list("set-cookie")
            if item.startswith("__Host-alpha_session=")
        ]
        assert len(session_cookies) == 1
        assert "Secure" in session_cookies[0] and "HttpOnly" in session_cookies[0]
        assert "Domain=" not in session_cookies[0]
