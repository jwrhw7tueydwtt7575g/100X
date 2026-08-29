"""Test configuration.

The environment is pinned *before* `app.config` is imported, so the suite never
picks up a developer's `.env`. Neither Postgres nor Redis is started: these
tests exercise the degraded path on purpose, which is exactly the behaviour
that must hold during the Wari when a dependency blips.
"""

from __future__ import annotations

import os

os.environ.update(
    {
        "ENVIRONMENT": "local",
        "DEBUG": "true",
        "LOG_JSON": "false",
        "LOG_LEVEL": "WARNING",
        "JWT_SECRET": "test-secret-not-for-production",
        "OPENAI_API_KEY": "",
        "LLM_ENABLED": "false",
        "OTP_DEBUG_ECHO": "true",
        "DEFAULT_LANGUAGE": "mr",
        # Point at hosts that are never reachable from the test runner.
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/wariverse_test",
        "REDIS_URL": "redis://127.0.0.1:1/0",
    }
)

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402

# Coordinates used across tests: the temple courtyard.
TEMPLE_LAT = 17.6786
TEMPLE_LON = 75.3300


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # No lifespan: the engine and Redis client are never created, so every
    # dependency yields None and the services take their fallback paths.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client
