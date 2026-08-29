"""Nearby-facility search (water, toilets, medical, food, shelter, …).

Postgres prefilters candidates with a bounding box on the (lat, lon) index and
the exact haversine distance is computed in Python — accurate to metres over
the ~250 km Wari corridor without requiring PostGIS.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.reference import FACILITIES, localized_name
from app.models.db_models import Facility
from app.models.schemas import FacilityOut
from app.services.geo import bounding_box, haversine_m, walk_minutes
from app.utils import is_open_now

log = structlog.get_logger(__name__)

# Types a pilgrim should still be routed to even when marked "closed".
_ALWAYS_RELEVANT = {"medical", "police"}


class FacilityService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def nearby(
        self,
        lat: float,
        lon: float,
        radius_m: int | None = None,
        facility_types: list[str] | None = None,
        limit: int = 20,
        language: str = "mr",
        open_only: bool = False,
        congestion_factor: float = 1.0,
    ) -> list[FacilityOut]:
        radius = min(radius_m or settings.facility_default_radius_m, settings.facility_max_radius_m)
        rows = await self._candidates(lat, lon, radius, facility_types)

        results: list[FacilityOut] = []
        for row in rows:
            distance = haversine_m(lat, lon, row["lat"], row["lon"])
            if distance > radius:
                continue

            is_open = is_open_now(row.get("opens_at"), row.get("closes_at"), row.get("is_24x7", False))
            if open_only and not is_open and row["facility_type"] not in _ALWAYS_RELEVANT:
                continue

            results.append(
                FacilityOut(
                    id=row.get("id"),
                    external_id=row.get("external_id"),
                    name=localized_name(row, language),
                    facility_type=row["facility_type"],
                    lat=row["lat"],
                    lon=row["lon"],
                    distance_m=int(round(distance)),
                    walk_minutes=walk_minutes(
                        distance, settings.walking_speed_kmph, congestion_factor
                    ),
                    address=row.get("address"),
                    contact_phone=row.get("contact_phone"),
                    is_open=is_open,
                    is_24x7=bool(row.get("is_24x7", False)),
                    opens_at=row.get("opens_at"),
                    closes_at=row.get("closes_at"),
                    capacity=row.get("capacity"),
                    wheelchair_accessible=bool(row.get("wheelchair_accessible", False)),
                    details=row.get("details"),
                )
            )

        results.sort(key=lambda f: f.distance_m)
        return results[:limit]

    async def nearest(
        self,
        lat: float,
        lon: float,
        facility_types: list[str],
        radius_m: int = 5000,
        language: str = "mr",
    ) -> FacilityOut | None:
        found = await self.nearby(
            lat, lon, radius_m=radius_m, facility_types=facility_types, limit=1, language=language
        )
        return found[0] if found else None

    # --- internals ---------------------------------------------------------

    async def _candidates(
        self, lat: float, lon: float, radius_m: int, facility_types: list[str] | None
    ) -> list[dict[str, Any]]:
        rows = await self._from_db(lat, lon, radius_m, facility_types)
        if rows:
            return rows

        # Empty database (fresh deploy, or Postgres unreachable): fall back to
        # the bundled reference dataset so safety lookups still answer.
        fallback = [dict(f) for f in FACILITIES if f.get("is_operational", True)]
        if facility_types:
            wanted = set(facility_types)
            fallback = [f for f in fallback if f["facility_type"] in wanted]
        return fallback

    async def _from_db(
        self, lat: float, lon: float, radius_m: int, facility_types: list[str] | None
    ) -> list[dict[str, Any]]:
        if self.db is None:
            return []

        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_m)
        stmt = select(Facility).where(
            Facility.is_operational.is_(True),
            Facility.lat.between(min_lat, max_lat),
            Facility.lon.between(min_lon, max_lon),
        )
        if facility_types:
            stmt = stmt.where(Facility.facility_type.in_(facility_types))

        try:
            facilities = (await self.db.execute(stmt.limit(200))).scalars().all()
        except Exception as exc:  # noqa: BLE001
            log.warning("facility_db_read_failed", error=str(exc))
            return []

        return [self._to_dict(f) for f in facilities]

    @staticmethod
    def _to_dict(facility: Facility) -> dict[str, Any]:
        return {
            "id": facility.id,
            "external_id": facility.external_id,
            "name_en": facility.name_en,
            "name_mr": facility.name_mr,
            "name_hi": facility.name_hi,
            "facility_type": facility.facility_type,
            "lat": facility.lat,
            "lon": facility.lon,
            "address": facility.address,
            "contact_phone": facility.contact_phone,
            "opens_at": facility.opens_at,
            "closes_at": facility.closes_at,
            "is_24x7": facility.is_24x7,
            "capacity": facility.capacity,
            "wheelchair_accessible": facility.wheelchair_accessible,
            "details": facility.details,
        }

    async def get_by_id(self, facility_id: UUID) -> Facility | None:
        if self.db is None:
            return None
        return (
            await self.db.execute(select(Facility).where(Facility.id == facility_id))
        ).scalar_one_or_none()
