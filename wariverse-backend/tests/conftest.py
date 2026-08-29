"""Test configuration.

The environment is pinned *before* `app.config` is imported, so the suite never
picks up a developer's `.env`.

By default neither Postgres nor Redis is started: those tests exercise the
degraded path on purpose, which is exactly the behaviour that must hold during
the Wari when a dependency blips.

Set `WARIVERSE_TEST_DATABASE_URL` (and optionally `WARIVERSE_TEST_REDIS_URL`)
to additionally run the integration tests in `test_auth_integration.py` against
a real, throwaway database. A dedicated variable rather than `DATABASE_URL`
means a developer's shell environment can never point the suite at a database
they care about.
"""

from __future__ import annotations

import os

INTEGRATION_DB_URL = os.environ.get("WARIVERSE_TEST_DATABASE_URL")
INTEGRATION_REDIS_URL = os.environ.get("WARIVERSE_TEST_REDIS_URL")

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
        "SMS_PROVIDER": "console",
        "DEFAULT_LANGUAGE": "mr",
        # Explicitly blank every credential the suite asserts on. Settings also
        # reads `.env`, so without this a developer who has a real ADMIN_API_KEY
        # or TWILIO_AUTH_TOKEN locally would see the "fails closed when
        # unconfigured" tests turn into 401s. Tests must not depend on whose
        # machine they run on.
        "ADMIN_API_KEY": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_PHONE_NUMBER": "",
        "CONTROL_ROOM_PHONE": "",
        "DEEPGRAM_API_KEY": "",
        "ELEVENLABS_API_KEY": "",
        "GOOGLE_TTS_API_KEY": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
        "IVR_PUBLIC_BASE_URL": "",
        "CROWD_SIMULATOR_ENABLED": "false",
        # Unreachable by default — port 1 is never listening.
        "DATABASE_URL": INTEGRATION_DB_URL
        or "postgresql+asyncpg://test:test@127.0.0.1:1/wariverse_test",
        "REDIS_URL": INTEGRATION_REDIS_URL or "redis://127.0.0.1:1/0",
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


@pytest.fixture
async def live_client() -> AsyncIterator[AsyncClient]:
    """A client with Postgres and Redis actually connected.

    ASGITransport does not run the app's lifespan, so the same startup and
    shutdown hooks are invoked directly here.
    """
    from app.db import close_db, init_db
    from app.redis_client import close_redis, init_redis

    assert await init_db(), "WARIVERSE_TEST_DATABASE_URL is set but unreachable"
    await init_redis()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            yield async_client
    finally:
        await close_redis()
        await close_db()
