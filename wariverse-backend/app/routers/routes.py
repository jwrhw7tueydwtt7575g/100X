"""Walking guidance along the palkhi route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.data.reference import DEFAULT_ROUTE_ID
from app.deps import Language, get_route_service
from app.models.schemas import RouteGuidanceResponse
from app.services.route_service import DestinationNotFoundError, RouteService

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get(
    "/guidance",
    response_model=RouteGuidanceResponse,
    summary="Step-by-step guidance from a coordinate to a halt or zone",
    responses={404: {"description": "Unknown destination or route"}},
)
async def get_guidance(
    routes: Annotated[RouteService, Depends(get_route_service)],
    language: Language,
    lat: Annotated[float, Query(ge=-90, le=90, examples=[17.6903])],
    lon: Annotated[float, Query(ge=-180, le=180, examples=[75.2787])],
    destination: Annotated[
        str | None,
        Query(
            description="Zone id or halt name, e.g. `vitthal_temple`, `wakhari`, "
            "`chandrabhaga_ghat`. Defaults to the temple.",
            max_length=64,
        ),
    ] = None,
    route_id: Annotated[str, Query(max_length=64)] = DEFAULT_ROUTE_ID,
) -> RouteGuidanceResponse:
    try:
        return await routes.guidance(
            lat=lat, lon=lon, destination=destination, route_id=route_id, language=language
        )
    except DestinationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown destination or route: {destination or route_id}",
        ) from exc
