"""Background task that keeps crowd readings moving.

Every `CROWD_SIMULATOR_INTERVAL_SECONDS` it walks the six zones, samples the
time-of-day curve, adds a little random variance, and records the result to
Redis and Postgres.

This is a **stand-in for the CCTV/drone ingestion pipeline**, and it labels its
rows `source="model"` so nothing downstream mistakes them for measurements.
Once the real feed writes `crowd_density_readings` with `source="camera"`, set
`CROWD_SIMULATOR_ENABLED=false` and delete nothing else — the read path already
prefers whatever row is newest.

⚠️ Run it in ONE process. With several uvicorn workers each would run its own
copy and they would overwrite each other's readings every few minutes. Either
keep `--workers 1`, enable it on a single instance, or move it to a scheduled
job that calls `CrowdService.record()`.
"""

from __future__ import annotations

import asyncio
import random

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.data.reference import ZONES
from app.db import get_engine
from app.services.crowd_service import CrowdService, curve_density

log = structlog.get_logger(__name__)

# How far a tick may drift from the curve, in density points.
VARIANCE = 6
# ...and how far it may move from the previous reading in one tick. Crowds
# build and thin gradually; a 40-point jump would be a sensor fault, not a fact.
MAX_STEP = 12

_task: asyncio.Task | None = None


def next_density(zone_id: str, previous: int | None) -> int:
    """Sample the curve for now, jittered, and rate-limited against `previous`."""
    target = curve_density(zone_id) + random.randint(-VARIANCE, VARIANCE)
    target = max(0, min(target, 100))

    if previous is None:
        return target
    step = max(-MAX_STEP, min(target - previous, MAX_STEP))
    return max(0, min(previous + step, 100))


async def tick() -> int:
    """One pass over every zone. Returns how many readings were written."""
    engine = get_engine()
    factory = (
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        if engine is not None
        else None
    )

    written = 0
    for zone in ZONES:
        zone_id = zone["zone_id"]
        session = factory() if factory else None
        try:
            service = CrowdService(session)
            previous = await service._read_cache(zone_id)
            density = next_density(zone_id, previous.density if previous else None)
            await service.record(zone_id, density, source="model")
            written += 1
        except Exception as exc:  # noqa: BLE001 — one bad zone must not stop the rest
            log.warning("crowd_tick_failed", zone_id=zone_id, error=str(exc))
        finally:
            if session is not None:
                await session.close()

    log.info("crowd_simulator_tick", zones_updated=written)
    return written


async def _run() -> None:
    interval = settings.crowd_simulator_interval_seconds
    log.info("crowd_simulator_started", interval_seconds=interval)
    try:
        while True:
            await tick()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("crowd_simulator_stopped")
        raise


def start() -> None:
    global _task
    if not settings.crowd_simulator_enabled:
        log.info("crowd_simulator_disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_run(), name="crowd-simulator")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown is best effort
        pass
    _task = None
