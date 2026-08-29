"""Structured JSON logging (structlog) plus per-request access logging."""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

REQUEST_ID_HEADER = "x-request-id"

# Endpoints whose bodies must never end up in logs verbatim.
_SENSITIVE_PATHS = ("/api/auth/otp/send", "/api/auth/otp/verify")


def configure_logging() -> None:
    """Route stdlib logging and structlog through one JSON (or console) renderer."""
    level = getattr(logging, settings.log_level, logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn keeps its own handlers; drop them so every line is JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Bind a request id to the log context and emit one access log per request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=_client_ip(request),
        )

        log = structlog.get_logger("api.request")
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "request_failed", duration_ms=round((time.perf_counter() - started) * 1000, 2)
            )
            structlog.contextvars.clear_contextvars()
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path != "/health":
            log.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
                query=_safe_query(request),
            )

        structlog.contextvars.clear_contextvars()
        return response


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _safe_query(request: Request) -> str | None:
    if request.url.path in _SENSITIVE_PATHS:
        return None
    return request.url.query or None
