"""Walking guidance to the temple."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.deps import Language, get_route_service
from app.models.schemas import RouteGuidanceResponse
from app.services.route_service import TEMPLE_LAT, TEMPLE_LON, RouteService

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get(
    "/guidance",
    response_model=RouteGuidanceResponse,
    summary="Pilgrim walking route, avoiding congested zones",
)
async def get_guidance(
    routes: Annotated[RouteService, Depends(get_route_service)],
    language: Language,
    origin_lat: Annotated[float, Query(ge=-90, le=90, examples=[17.6790])],
    origin_lng: Annotated[float, Query(ge=-180, le=180, examples=[75.3245])],
    dest_lat: Annotated[
        float, Query(ge=-90, le=90, description="Defaults to the Vitthal temple.")
    ] = TEMPLE_LAT,
    dest_lng: Annotated[float, Query(ge=-180, le=180)] = TEMPLE_LON,
) -> RouteGuidanceResponse:
    return await routes.guidance(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        dest_lat=dest_lat,
        dest_lng=dest_lng,
        language=language,
    )
