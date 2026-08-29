"""WariVerse API — FastAPI entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import close_db, init_db, ping_db
from app.middleware.logging import RequestLoggingMiddleware, configure_logging
from app.models.schemas import (
    ComponentHealth,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ReadinessResponse,
)
from app.redis_client import close_redis, init_redis, ping_redis
from app.routers import (
    admin,
    auth,
    community,
    community_facilities,
    conversation,
    crowd,
    facilities,
    ivr,
    lost_found,
    routes,
    sos,
    temple,
    voice,
)
from app.services import crowd_simulator

log = structlog.get_logger(__name__)

DESCRIPTION = """
Backend for **WariVerse**, a multilingual AI assistant for pilgrims walking the
Pandharpur Wari.

* Conversational assistant in Marathi, Hindi and English
* Live crowd density per zone, with quieter alternatives
* Nearby water, toilets, medical posts, annachhatras and shelters
* Walking guidance along the palkhi route
* Lost & found reports and emergency SOS dispatch

All request and response bodies are snake_case JSON.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "startup_begin",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

    # Neither dependency is fatal at boot: the API degrades to cached/seeded
    # data rather than leaving pilgrims without safety information. /health/ready
    # reports the real state for the orchestrator to act on.
    db_ok = await init_db()
    redis_ok = await init_redis()

    for problem in settings.insecure_secrets:
        # Loud in every environment, because a deploy that reaches production
        # with a default secret is the kind of thing nobody notices until it
        # matters.
        (log.error if settings.is_production else log.warning)(
            "insecure_configuration", detail=problem
        )

    # Stands in for the CCTV feed. Runs per process, so keep it on one worker —
    # see app/services/crowd_simulator.py.
    crowd_simulator.start()

    log.info(
        "startup_complete",
        database="ok" if db_ok else "degraded",
        redis="ok" if redis_ok else "degraded",
        llm="ok" if settings.llm_configured else "disabled",
        crowd_simulator="on" if settings.crowd_simulator_enabled else "off",
    )

    yield

    await crowd_simulator.stop()
    await close_redis()
    await close_db()
    log.info("shutdown_complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Added last → outermost, so CORS headers are present on error responses too.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
    max_age=600,
)


# --- error handling ---------------------------------------------------------


def _error(request: Request, code: str, message: str, status_code: int, details=None):
    payload = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return _error(
        request,
        code=f"http_{exc.status_code}",
        message=str(exc.detail),
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(
        request,
        code="validation_error",
        message="request validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"errors": _serializable_errors(exc)},
    )


def _serializable_errors(exc: RequestValidationError) -> list[dict]:
    """Pydantic puts the original exception object in `ctx`, which JSON cannot
    encode — a custom validator raising ValueError would otherwise turn a 422
    into a 500. Stringify `ctx` and drop the docs URL."""
    cleaned: list[dict] = []
    for error in exc.errors():
        item = {k: v for k, v in error.items() if k not in ("ctx", "url")}
        if "ctx" in error:
            item["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        cleaned.append(item)
    return cleaned


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled_exception", path=request.url.path)
    return _error(
        request,
        code="internal_error",
        message="an unexpected error occurred",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# --- health -----------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["health"], summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)


@app.get(
    "/health/ready",
    response_model=ReadinessResponse,
    tags=["health"],
    summary="Readiness probe with dependency status",
)
async def readiness() -> ReadinessResponse:
    components = [
        ComponentHealth(name="database", status="ok" if await ping_db() else "down"),
        ComponentHealth(name="redis", status="ok" if await ping_redis() else "down"),
        ComponentHealth(
            name="llm",
            status="ok" if settings.llm_configured else "disabled",
            detail=settings.openai_model if settings.llm_configured else "rule-based replies",
        ),
    ]
    degraded = any(c.status == "down" for c in components)
    return ReadinessResponse(
        status="degraded" if degraded else "ok",
        version=settings.app_version,
        environment=settings.environment,
        components=components,
    )


# --- routes -----------------------------------------------------------------

for module in (
    conversation, crowd, facilities, routes, temple, lost_found, sos, auth, admin,
    ivr, voice, community, community_facilities,
):
    app.include_router(module.router, prefix=settings.api_prefix)
