"""Crowd density for a monitored zone."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.deps import Language, get_crowd_service
from app.models.schemas import CrowdResponse
from app.services.crowd_service import CrowdService, ZoneNotFoundError

router = APIRouter(prefix="/crowd", tags=["crowd"])


@router.get(
    "/{zone_id}",
    response_model=CrowdResponse,
    summary="Current crowd level for a zone",
    responses={404: {"description": "Unknown zone_id"}},
)
async def get_crowd(
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    language: Language,
    zone_id: Annotated[
        str,
        Path(
            description="Registered zone identifier, e.g. `vitthal_temple`.",
            examples=["vitthal_temple"],
            max_length=64,
        ),
    ],
) -> CrowdResponse:
    try:
        return await crowd.get_zone(zone_id, language)
    except ZoneNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown zone_id: {zone_id}"
        ) from exc
