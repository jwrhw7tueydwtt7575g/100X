"""Seed reference data (zones, facilities, palkhi waypoints) into Postgres.

Idempotent: existing rows are updated in place by their natural key, so it is
safe to run on every deploy. Run with `python -m scripts.seed`.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.reference import (
    DEFAULT_ROUTE_ID,
    FACILITIES,
    ROUTE_WAYPOINTS,
    ZONES,
)
from app.db import close_db, create_engine
from app.middleware.logging import configure_logging
from app.models.db_models import Facility, RouteWaypoint, Zone

log = structlog.get_logger("seed")


async def seed_zones(session: AsyncSession) -> tuple[int, int]:
    created = updated = 0
    for record in ZONES:
        existing = (
            await session.execute(select(Zone).where(Zone.zone_id == record["zone_id"]))
        ).scalar_one_or_none()
        if existing is None:
            session.add(Zone(**record))
            created += 1
        else:
            for key, value in record.items():
                setattr(existing, key, value)
            updated += 1
    return created, updated


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
        zones = await seed_zones(session)
        facilities = await seed_facilities(session)
        waypoints = await seed_waypoints(session)
        await session.commit()

    log.info(
        "seed_complete",
        zones_created=zones[0],
        zones_updated=zones[1],
        facilities_created=facilities[0],
        facilities_updated=facilities[1],
        waypoints_created=waypoints[0],
        waypoints_updated=waypoints[1],
    )
    await engine.dispose()
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
