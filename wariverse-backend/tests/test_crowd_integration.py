"""Crowd storage, the simulator tick, and the admin override against real services.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import desc, select

from app.models.db_models import CrowdDensityReading
from app.services.crowd_service import (
    CACHE_PREFIX,
    CACHE_TTL_SECONDS,
    CrowdService,
    density_to_status,
)
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)

API_KEY = "integration-admin-key"


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def _latest(zone_id: str) -> CrowdDensityReading | None:
    async with await _db() as db:
        return (
            await db.execute(
                select(CrowdDensityReading)
                .where(CrowdDensityReading.zone_id == zone_id)
                .order_by(desc(CrowdDensityReading.recorded_at))
                .limit(1)
            )
        ).scalar_one_or_none()


# --- storage ----------------------------------------------------------------


async def test_record_writes_redis_and_postgres(live_client: AsyncClient) -> None:
    from app.redis_client import get_redis

    async with await _db() as db:
        await CrowdService(db).record("gate-1", 77, source="manual")

    cached = await get_redis().get(f"{CACHE_PREFIX}gate-1")
    assert cached is not None, "the reading must be cached for fast reads"

    row = await _latest("gate-1")
    assert row.density == 77
    assert row.status == "HIGH"
    assert row.source == "manual"


async def test_cached_reading_expires_after_five_minutes(
    live_client: AsyncClient,
) -> None:
    from app.redis_client import get_redis

    async with await _db() as db:
        await CrowdService(db).record("gate-2", 44)

    ttl = await get_redis().ttl(f"{CACHE_PREFIX}gate-2")
    assert 0 < ttl <= CACHE_TTL_SECONDS
    # Stale crowd data is worse than none; the window must stay short.
    assert CACHE_TTL_SECONDS == 300


async def test_reads_prefer_the_cache_over_the_curve(live_client: AsyncClient) -> None:
    async with await _db() as db:
        await CrowdService(db).record("gate-3", 91, source="camera")

    body = (await live_client.get("/api/crowd/gate-3", params={"language": "en"})).json()
    assert body["density"] == 91
    assert body["status"] == "VERY_HIGH"


async def test_reading_survives_a_cache_flush_via_postgres(
    live_client: AsyncClient,
) -> None:
    from app.redis_client import get_redis

    async with await _db() as db:
        await CrowdService(db).record("main-road", 55, source="camera")

    await get_redis().delete(f"{CACHE_PREFIX}main-road")

    body = (await live_client.get("/api/crowd/main-road")).json()
    assert body["density"] == 55, "the stored row should back-fill the cache"


async def test_every_reading_is_kept_as_history(live_client: AsyncClient) -> None:
    async with await _db() as db:
        service = CrowdService(db)
        for density in (20, 40, 60):
            await service.record("bhima-ghat", density, source="manual")

        rows = (
            (
                await db.execute(
                    select(CrowdDensityReading).where(
                        CrowdDensityReading.zone_id == "bhima-ghat"
                    )
                )
            )
            .scalars()
            .all()
        )
    # The table is an append-only log — the forecaster will need this history.
    assert len([r for r in rows if r.density in (20, 40, 60)]) >= 3


async def test_trend_is_derived_from_the_previous_reading(
    live_client: AsyncClient,
) -> None:
    async with await _db() as db:
        service = CrowdService(db)
        await service.record("gate-1", 30, source="manual")
        rising = await service.record("gate-1", 70, source="manual")
        falling = await service.record("gate-1", 20, source="manual")

    assert rising.trend == "rising"
    assert falling.trend == "falling"


# --- the simulator ----------------------------------------------------------


async def test_simulator_tick_updates_every_zone(live_client: AsyncClient) -> None:
    from app.services.crowd_simulator import tick

    written = await tick()
    assert written == 6

    for zone_id in ("gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road"):
        row = await _latest(zone_id)
        assert row is not None
        assert row.source == "model"
        assert row.status == density_to_status(row.density)


async def test_consecutive_ticks_move_gradually(live_client: AsyncClient) -> None:
    from app.services.crowd_simulator import MAX_STEP, tick

    await tick()
    first = (await live_client.get("/api/crowd/temple-main")).json()["density"]
    await tick()
    second = (await live_client.get("/api/crowd/temple-main")).json()["density"]

    # A crowd builds and thins; a huge jump between ticks would look like a fault.
    assert abs(second - first) <= MAX_STEP


# --- admin override ---------------------------------------------------------


async def test_admin_override_wins_over_the_model(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)

    response = await live_client.post(
        "/api/admin/crowd/gate-2",
        json={"density": 95},
        headers={"X-API-Key": API_KEY},
        params={"language": "en"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["density"] == 95
    assert body["status"] == "VERY_HIGH"
    assert body["zone_name"] == "Gate 2"

    # And a subsequent read sees it.
    assert (await live_client.get("/api/crowd/gate-2")).json()["density"] == 95

    row = await _latest("gate-2")
    assert row.density == 95
    assert row.source == "manual"


async def test_admin_override_is_visible_in_the_all_endpoint(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    await live_client.post(
        "/api/admin/crowd/main-road",
        json={"density": 12},
        headers={"X-API-Key": API_KEY},
    )

    zones = (await live_client.get("/api/crowd/all")).json()
    main_road = next(z for z in zones if z["zone_id"] == "main-road")
    assert main_road["density"] == 12
    assert main_road["status"] == "LOW"


async def test_admin_override_shifts_the_forecast_start(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    await live_client.post(
        "/api/admin/crowd/gate-3",
        json={"density": 88},
        headers={"X-API-Key": API_KEY},
    )

    forecast = (await live_client.get("/api/crowd/gate-3/forecast")).json()
    # The forecast must start from what the pilgrim can see, not the curve.
    assert abs(forecast["points"][0]["value"] - 88) <= 2


async def test_admin_rejects_an_unknown_zone(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    response = await live_client.post(
        "/api/admin/crowd/atlantis",
        json={"density": 50},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 404
