"""Nearby facility search."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.deps import Language, get_facility_service
from app.models.schemas import FacilityCategory, FacilityNearbyResponse
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
    lat: Annotated[float, Query(ge=-90, le=90, examples=[17.6775])],
    lng: Annotated[float, Query(ge=-180, le=180, examples=[75.3283])],
    category: Annotated[
        list[FacilityCategory] | None,
        Query(description="Repeat to request several categories. Omit for all."),
    ] = None,
    radius_m: Annotated[int, Query(ge=50, le=50_000)] = 1000,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    open_only: Annotated[
        bool, Query(description="Hide closed places (medical is always shown).")
    ] = False,
) -> FacilityNearbyResponse:
    radius = min(radius_m, settings.facility_max_radius_m)
    results = await facilities.nearby(
        lat=lat,
        lon=lng,
        radius_m=radius,
        # Without a category filter, show only what pilgrims search for —
        # police posts and lost & found desks are reached other ways.
        facility_types=list(category) if category else list(settings.facility_categories),
        limit=limit,
        language=language,
        open_only=open_only,
    )
    return FacilityNearbyResponse(facilities=results)
