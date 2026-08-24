from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .security import PasswordWorkBusy

logger = logging.getLogger("alpha.api")


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        super().__init__(code)


def error_body(request: Request, code: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", "unknown"),
        }
    }


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        try:
            request.state.request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
        except ValueError:
            request.state.request_id = str(uuid.uuid4())
        started = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "request_complete",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(request, exc.code, exc.message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.info("request_validation_failed", extra={"request_id": request.state.request_id})
        return JSONResponse(
            status_code=422,
            content=error_body(request, "validation_error", "요청 형식이 올바르지 않습니다."),
        )

    @app.exception_handler(OperationalError)
    async def handle_database_operational_error(request: Request, _exc: OperationalError) -> JSONResponse:
        # Database exception text may contain connection details or statement
        # parameters. Keep both the client response and structured log generic.
        logger.warning(
            "database_temporarily_unavailable",
            extra={"request_id": request.state.request_id},
        )
        return JSONResponse(
            status_code=503,
            content=error_body(
                request,
                "database_temporarily_unavailable",
                "데이터 저장소가 일시적으로 혼잡합니다. 잠시 후 다시 시도하세요.",
            ),
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(PasswordWorkBusy)
    async def handle_password_work_busy(request: Request, _exc: PasswordWorkBusy) -> JSONResponse:
        logger.warning(
            "password_service_busy",
            extra={"request_id": request.state.request_id},
        )
        return JSONResponse(
            status_code=503,
            content=error_body(
                request,
                "password_service_busy",
                "인증 처리량이 일시적으로 포화되었습니다. 잠시 후 다시 시도하세요.",
            ),
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"request_id": request.state.request_id})
        return JSONResponse(
            status_code=500,
            content=error_body(request, "internal_error", "요청 처리 중 오류가 발생했습니다."),
        )
