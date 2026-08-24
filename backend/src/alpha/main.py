from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .config import Settings, get_settings
from .db import Database
from .dependencies import get_db
from .errors import RequestContextMiddleware, error_body, install_error_handlers
from .limits import (
    MAX_ACTIVE_SESSIONS_PER_USER,
    MAX_CHALLENGE_ATTEMPTS,
    MAX_MEMBERS_PER_TEAM,
    MAX_PARTICIPANT_USERS,
    MAX_PUBLIC_SCOREBOARD_ENTRIES,
    MAX_SUBMISSIONS_PER_TEAM_EVENT,
)
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_participant import router as participant_router
from .routes_teams import router as teams_router
from .security import csrf_context, verify_csrf
from .store import EphemeralStore, create_store

logger = logging.getLogger("alpha")


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_chunks = 4096

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        response = JSONResponse(
            status_code=413,
            content=error_body(
                request,
                "request_too_large",
                "요청 본문이 허용 크기를 초과했습니다.",
            ),
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lengths: list[int] = []
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() != b"content-length":
                continue
            try:
                value = int(raw_value)
            except ValueError:
                value = self.max_bytes + 1
            lengths.append(value)
        if len(lengths) > 1 or any(value < 0 or value > self.max_bytes for value in lengths):
            await self._reject(scope, receive, send)
            return

        received = 0
        chunks = 0
        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            received += len(chunk)
            chunks += 1
            if received > self.max_bytes or chunks > self.max_chunks:
                await self._reject(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            await send(message)

        await self.app(scope, receive, secure_send)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/api/v1/") and request.method.upper() not in {
            "GET",
            "HEAD",
            "OPTIONS",
        }:
            settings: Settings = request.app.state.settings
            cookie = request.cookies.get(settings.csrf_cookie_name, "")
            header = request.headers.get("X-CSRF-Token", "")
            session_token = request.cookies.get(settings.session_cookie_name, "")
            browser_token = request.cookies.get(settings.browser_cookie_name, "")
            context = csrf_context(
                settings.secret_key.get_secret_value(),
                session_token=session_token,
                browser_token=browser_token,
            )
            valid = bool(cookie and header)
            valid = valid and hmac.compare_digest(cookie, header)
            valid = valid and bool(context)
            valid = valid and verify_csrf(
                settings.secret_key.get_secret_value(),
                header,
                settings.csrf_ttl_seconds,
                context,
            )
            if not valid:
                return JSONResponse(
                    status_code=403,
                    content=error_body(
                        request, "csrf_failed", "CSRF 검증에 실패했습니다. 새 토큰을 받아 다시 시도하세요."
                    ),
                )
        return await call_next(request)


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    store: EphemeralStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    store = store or create_store(settings.redis_url)
    production = settings.environment == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.store.close()
        app.state.db.dispose()

    app = FastAPI(
        title="CTFnight API",
        version=__version__,
        docs_url=None if production else "/api/docs",
        redoc_url=None if production else "/api/redoc",
        openapi_url=None if production else "/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database
    app.state.store = store

    # Starlette wraps the most recently added middleware around earlier ones.
    # Body reads therefore stay inside cheap Host/CSRF checks, while CORS and
    # security headers still decorate every structured rejection.
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-CSRF-Token", "X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    system = APIRouter(tags=["system"])

    @system.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @system.get("/health/ready", response_model=None)
    def readiness(request: Request, db: Session = Depends(get_db)) -> Response | dict[str, str]:
        checks = {"database": False, "redis": False}
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            logger.exception("database_readiness_failed")
        try:
            checks["redis"] = bool(request.app.state.store.ping())
        except Exception:
            logger.exception("redis_readiness_failed")
        if all(checks.values()):
            return {"status": "ok"}
        body = error_body(request, "not_ready", "필수 저장소 연결이 준비되지 않았습니다.")
        body["error"]["checks"] = checks
        return JSONResponse(status_code=503, content=body)

    @system.get("/meta")
    def meta() -> dict:
        return {
            "name": "CTFnight",
            "version": __version__,
            "api_version": "v1",
            "session_cookie": settings.session_cookie_name,
            "csrf_cookie": settings.csrf_cookie_name,
            "csrf_header": "X-CSRF-Token",
            "limits": {
                "max_flag_length": settings.max_flag_length,
                "max_request_body_bytes": settings.max_request_body_bytes,
                "max_submissions_per_team_challenge": MAX_CHALLENGE_ATTEMPTS,
                "max_submissions_per_team_event": MAX_SUBMISSIONS_PER_TEAM_EVENT,
                "max_members_per_team": MAX_MEMBERS_PER_TEAM,
                "max_participant_users": MAX_PARTICIPANT_USERS,
                "max_active_sessions_per_user": MAX_ACTIVE_SESSIONS_PER_USER,
                "max_public_scoreboard_entries": MAX_PUBLIC_SCOREBOARD_ENTRIES,
            },
            "features": {
                "teams": True,
                "dynamic_scoring": True,
                "challenge_runtime": False,
                "attack_defense": False,
            },
        }

    api = APIRouter(prefix="/api/v1")
    api.include_router(system)
    api.include_router(auth_router)
    api.include_router(teams_router)
    api.include_router(participant_router)
    api.include_router(admin_router)
    app.include_router(api)
    return app


app = create_app()
