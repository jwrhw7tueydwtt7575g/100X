"""Seed reference data into Postgres.

Populates:
  * an opening `crowd_density_readings` row for each of the six Pandharpur
    zones — gate-1, gate-2, gate-3, temple-main, bhima-ghat, main-road — so the
    crowd endpoints return `source: "live"` before the camera feed is wired up;
  * the facility directory (water, toilets, medical, food, shelter, …);
  * the Alandi → Pandharpur palkhi waypoints.

Idempotent: facilities and waypoints are updated in place by their natural key,
and a zone only gets a new reading if it has none, so running this on every
deploy neither duplicates rows nor overwrites live sensor data.

Run with `python -m scripts.seed`.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.reference import (
    DEFAULT_ROUTE_ID,
    FACILITIES,
    ROUTE_WAYPOINTS,
    ZONES,
)
from app.services.temple_service import TempleService
from app.db import close_db, create_engine
from app.middleware.logging import configure_logging
from app.models.db_models import (
    CrowdDensityReading,
    Facility,
    RouteWaypoint,
)
from app.services.crowd_service import density_to_status

log = structlog.get_logger("seed")

# Opening density per zone, as a percentage of capacity. Plausible mid-morning
# values on a normal (non-Ekadashi) Wari day; the ingestion pipeline overwrites
# these as soon as it starts publishing.
SEED_DENSITY: dict[str, int] = {
    "gate-1": 55,
    "gate-2": 72,
    "gate-3": 38,
    "temple-main": 68,
    "bhima-ghat": 45,
    "main-road": 30,
}


async def seed_crowd_readings(session: AsyncSession) -> tuple[int, int]:
    """One baseline reading per zone, skipped where readings already exist."""
    created = skipped = 0
    for zone in ZONES:
        zone_id = zone["zone_id"]
        existing = (
            await session.execute(
                select(func.count())
                .select_from(CrowdDensityReading)
                .where(CrowdDensityReading.zone_id == zone_id)
            )
        ).scalar_one()
        if existing:
            skipped += 1
            continue

        density = SEED_DENSITY.get(zone_id, 40)
        session.add(
            CrowdDensityReading(
                zone_id=zone_id,
                zone_name=zone["name_en"],
                density=density,
                status=density_to_status(density),
                latitude=zone["lat"],
                longitude=zone["lon"],
                source="model",
            )
        )
        created += 1
    return created, skipped


async def seed_facilities(session: AsyncSession) -> tuple[int, int]:
    created = updated = 0
    for record in FACILITIES:
        existing = (
            await session.execute(
                select(Facility).where(Facility.external_id == record["external_id"])
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Facility(**record))
            created += 1
        else:
            for key, value in record.items():
                setattr(existing, key, value)
            updated += 1
    return created, updated


async def seed_waypoints(session: AsyncSession) -> tuple[int, int]:
    created = updated = 0
    for record in ROUTE_WAYPOINTS:
        existing = (
            await session.execute(
                select(RouteWaypoint).where(
                    RouteWaypoint.route_id == DEFAULT_ROUTE_ID,
                    RouteWaypoint.sequence == record["sequence"],
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(RouteWaypoint(route_id=DEFAULT_ROUTE_ID, **record))
            created += 1
        else:
            for key, value in record.items():
                setattr(existing, key, value)
            updated += 1
    return created, updated


async def main() -> None:
    configure_logging()
    engine = create_engine()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        readings = await seed_crowd_readings(session)
        facilities = await seed_facilities(session)
        waypoints = await seed_waypoints(session)
        await session.commit()
        temple_rows = await TempleService(session).seed_defaults()

    log.info(
        "seed_complete",
        crowd_readings_created=readings[0],
        crowd_readings_skipped=readings[1],
        facilities_created=facilities[0],
        facilities_updated=facilities[1],
        waypoints_created=waypoints[0],
        waypoints_updated=waypoints[1],
        temple_info_created=temple_rows,
    )
    await engine.dispose()
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
