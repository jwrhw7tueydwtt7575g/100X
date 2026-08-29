"""Nearby facility search."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.deps import Language, get_facility_service
from app.models.schemas import FacilityNearbyResponse, FacilityType, GeoPoint
from app.services.facility_service import FacilityService

router = APIRouter(prefix="/facilities", tags=["facilities"])


@router.get(
    "/nearby",
    response_model=FacilityNearbyResponse,
    summary="Facilities near a coordinate, nearest first",
)
async def get_nearby(
    facilities: Annotated[FacilityService, Depends(get_facility_service)],
    language: Language,
    lat: Annotated[float, Query(ge=-90, le=90, examples=[17.6786])],
    lon: Annotated[float, Query(ge=-180, le=180, examples=[75.3300])],
    radius_m: Annotated[int | None, Query(ge=50, le=50_000)] = None,
    facility_type: Annotated[
        list[FacilityType] | None,
        Query(description="Repeat to request several types. Omit for all types."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    open_only: Annotated[
        bool, Query(description="Hide closed facilities (medical and police are always shown).")
    ] = False,
) -> FacilityNearbyResponse:
    radius = min(
        radius_m or settings.facility_default_radius_m, settings.facility_max_radius_m
    )
    results = await facilities.nearby(
        lat=lat,
        lon=lon,
        radius_m=radius,
        facility_types=list(facility_type) if facility_type else None,
        limit=limit,
        language=language,
        open_only=open_only,
    )
    return FacilityNearbyResponse(
        origin=GeoPoint(lat=lat, lon=lon),
        radius_m=radius,
        count=len(results),
        facility_types=list(facility_type) if facility_type else [],
        language=language,  # type: ignore[arg-type]
        facilities=results,
    )
