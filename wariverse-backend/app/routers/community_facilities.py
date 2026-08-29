"""Public Community Facilities API.

Allows trusts, NGOs, volunteers, and local mandals to register free services
(food, stay, rest points, water, medical aid) along the Wari route.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import DbSession
from app.models.db_models import CommunityFacility
from app.services.geo import bounding_box, haversine_m

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/community-facilities", tags=["community-facilities"])


class CommunityFacilityCreate(BaseModel):
    category: str = Field(..., description="medical | food | accommodation | water | toilet | rest")
    name: str = Field(..., max_length=150)
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    phone: str | None = Field(None, max_length=20)
    type: str = Field("charity_food", description="charity_food | charity_stay | seeded_public")
    added_by: str | None = Field(None, max_length=100)


class CommunityFacilityOut(BaseModel):
    id: str
    category: str
    name: str
    lat: float
    lon: float
    phone: str | None = None
    type: str
    is_active: bool
    added_by: str | None = None


@router.post(
    "",
    response_model=CommunityFacilityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a free community facility",
)
async def create_facility(
    payload: CommunityFacilityCreate, db: DbSession
) -> CommunityFacilityOut:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        )

    facility = CommunityFacility(
        category=payload.category,
        name=payload.name,
        lat=payload.lat,
        lon=payload.lon,
        phone=payload.phone,
        type=payload.type,
        is_active=True,
        added_by=payload.added_by,
    )
    db.add(facility)
    await db.commit()
    await db.refresh(facility)

    log.info("community_facility_registered", id=facility.id, name=facility.name)
    return CommunityFacilityOut(
        id=facility.id,
        category=facility.category,
        name=facility.name,
        lat=facility.lat,
        lon=facility.lon,
        phone=facility.phone,
        type=facility.type,
        is_active=facility.is_active,
        added_by=facility.added_by,
    )


@router.get(
    "",
    response_model=list[CommunityFacilityOut],
    summary="List active community facilities",
)
async def list_facilities(
    db: DbSession,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[int | None, Query(ge=50, le=100_000)] = 5000,
) -> list[CommunityFacilityOut]:
    if db is None:
        return []

    stmt = select(CommunityFacility).where(CommunityFacility.is_active.is_(True))
    if lat is not None and lng is not None and radius_m:
        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lng, radius_m)
        stmt = stmt.where(
            CommunityFacility.lat.between(min_lat, max_lat),
            CommunityFacility.lon.between(min_lon, max_lon),
        )

    try:
        rows = (await db.execute(stmt.limit(200))).scalars().all()
    except Exception as exc:
        log.warning("community_facilities_read_failed", error=str(exc))
        return []

    if lat is not None and lng is not None and radius_m:
        rows = [r for r in rows if haversine_m(lat, lng, r.lat, r.lon) <= radius_m]

    return [
        CommunityFacilityOut(
            id=r.id,
            category=r.category,
            name=r.name,
            lat=r.lat,
            lon=r.lon,
            phone=r.phone,
            type=r.type,
            is_active=r.is_active,
            added_by=r.added_by,
        )
        for r in rows
    ]
