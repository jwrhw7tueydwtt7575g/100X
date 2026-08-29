"""PostgreSQL async engine and session management.

The engine is created lazily at startup. Every dependency yields
`AsyncSession | None`: during the Wari the API must keep answering safety
questions from cached/seeded data even if Postgres is briefly unreachable, so
callers degrade instead of 500-ing. Endpoints that genuinely cannot work
without the database raise 503 explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model and by Alembic autogenerate."""

    type_annotation_map: dict[Any, Any] = {}


def create_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
    )


async def init_db() -> bool:
    """Create the engine and verify connectivity. Returns True when usable."""
    global _engine, _session_factory

    if _engine is None:
        _engine = create_engine()
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )

    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — startup must not hard-crash the pod
        log.error("db_connect_failed", error=str(exc))
        return False

    log.info("db_connected", pool_size=settings.db_pool_size)
    return True


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log.info("db_disposed")
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine | None:
    return _engine


async def ping_db() -> bool:
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


async def get_db() -> AsyncIterator[AsyncSession | None]:
    """FastAPI dependency yielding a session, or None when the DB is down."""
    if _session_factory is None:
        yield None
        return

    session = _session_factory()
    try:
        yield session
        # Routers commit explicitly; this only flushes implicit work.
        if session.in_transaction():
            await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
