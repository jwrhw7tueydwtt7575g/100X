"""Crowd density for the six monitored Pandharpur zones."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.data.i18n import t
from app.data.reference import ZONES_BY_ID
from app.deps import Language, get_crowd_service
from app.models.schemas import (
    CrowdForecastResponse,
    CrowdResponse,
    ForecastPoint,
)
from app.services.crowd_service import CrowdService, ZoneNotFoundError, ZoneReading
from app.utils import humanize_age, now_utc

router = APIRouter(prefix="/crowd", tags=["crowd"])

ZoneId = Annotated[
    str,
    Path(
        description="One of: " + ", ".join(ZONES_BY_ID),
        examples=["gate-3"],
        max_length=50,
    ),
]


def _to_response(reading: ZoneReading, language: str) -> CrowdResponse:
    return CrowdResponse(
        zone_id=reading.zone_id,
        zone_name=reading.zone_name,
        density=reading.density,
        status=reading.status,  # type: ignore[arg-type]
        latitude=reading.latitude,
        longitude=reading.longitude,
        updated_at=humanize_age(reading.recorded_at, language),
    )


# `/all` is declared before `/{zone_id}` because FastAPI matches in order —
# otherwise "all" would be captured as a zone id and 404.
@router.get(
    "/all",
    response_model=list[CrowdResponse],
    summary="Current density for every monitored zone",
)
async def get_all_zones(
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    language: Language,
) -> list[CrowdResponse]:
    readings = await crowd.read_all(language)
    return [_to_response(reading, language) for reading in readings]


@router.get(
    "/{zone_id}",
    response_model=CrowdResponse,
    summary="Current density for one zone",
    responses={404: {"description": "Unknown zone_id"}},
)
async def get_zone(
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    language: Language,
    zone_id: ZoneId,
) -> CrowdResponse:
    try:
        reading = await crowd.read_zone(zone_id, language)
    except ZoneNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown zone_id: {zone_id}. Known zones: {', '.join(ZONES_BY_ID)}",
        ) from exc
    return _to_response(reading, language)


@router.get(
    "/{zone_id}/forecast",
    response_model=CrowdForecastResponse,
    summary="Hourly crowd projection for the next 12 hours",
    responses={404: {"description": "Unknown zone_id"}},
)
async def get_forecast(
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    language: Language,
    zone_id: ZoneId,
    hours: Annotated[int, Query(ge=1, le=24)] = 12,
) -> CrowdForecastResponse:
    try:
        points = await crowd.forecast(zone_id, hours=hours, language=language)
    except ZoneNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown zone_id: {zone_id}"
        ) from exc

    from app.data.reference import localized_name

    return CrowdForecastResponse(
        zone_id=zone_id,
        zone_name=localized_name(ZONES_BY_ID[zone_id], language),
        points=[ForecastPoint(time=p["time"], value=p["value"]) for p in points],
        recommendation=crowd.recommendation(zone_id, points, language),
        updated_at=t(
            "forecast_updated", language, age=humanize_age(now_utc(), language)
        ),
    )
