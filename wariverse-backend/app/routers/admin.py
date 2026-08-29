"""Operator endpoints, protected by a shared API key.

The key is compared with `hmac.compare_digest`, and an **unset** `ADMIN_API_KEY`
refuses every request rather than leaving the endpoints open — a misconfigured
deploy should fail closed, not silently publish a write endpoint that can move
two million people.
"""

from __future__ import annotations

import hmac
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.config import settings
from app.data.reference import ZONES_BY_ID
from app.deps import (
    DbSession,
    Language,
    get_crowd_service,
    get_sos_service,
    get_temple_service,
)
from app.models.schemas import (
    AdminCrowdUpdate,
    CrowdResponse,
    SosEventAdmin,
    TempleInfoResponse,
    TempleInfoUpdate,
)
from app.routers.crowd import ZoneId, _to_response
from app.services.crowd_service import CrowdService, ZoneNotFoundError
from app.services.sos_service import SosService
from app.services.temple_service import TempleService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    if not settings.admin_api_key:
        log.error("admin_api_key_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin endpoints are not configured",
        )
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing API key"
        )
    return x_api_key


ApiKey = Annotated[str, Depends(require_api_key)]


@router.post(
    "/crowd/{zone_id}",
    response_model=CrowdResponse,
    summary="Manually set a zone's crowd density",
    responses={
        401: {"description": "Invalid or missing X-API-Key"},
        404: {"description": "Unknown zone_id"},
        503: {"description": "ADMIN_API_KEY is not configured"},
    },
)
async def update_crowd(
    payload: AdminCrowdUpdate,
    zone_id: ZoneId,
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    db: DbSession,
    language: Language,
    _: ApiKey,
) -> CrowdResponse:
    """Override a reading — a marshal on the ground correcting the model.

    The value is written to Redis and to `crowd_density_readings`, so it wins
    until the next simulator tick or camera reading supersedes it.
    """
    if zone_id not in ZONES_BY_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown zone_id: {zone_id}"
        )
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="reading store unavailable; please retry shortly",
        )

    try:
        reading = await crowd.record(zone_id, payload.density, source=payload.source)
    except ZoneNotFoundError as exc:  # pragma: no cover — guarded above
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    log.warning(
        "admin_crowd_override",
        zone_id=zone_id,
        density=payload.density,
        status=reading.status,
        source=payload.source,
    )
    return _to_response(reading, language)


@router.get(
    "/sos/active",
    response_model=list[SosEventAdmin],
    summary="Every unresolved emergency, newest first",
    responses={
        401: {"description": "Invalid or missing X-API-Key"},
        503: {"description": "ADMIN_API_KEY unset, or the store is unavailable"},
    },
)
async def list_active_sos(
    sos: Annotated[SosService, Depends(get_sos_service)],
    db: DbSession,
    _: ApiKey,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SosEventAdmin]:
    """The dashboard's poll endpoint.

    Reads Postgres, so it lists emergencies even when the `sos:new` pub/sub
    push failed — which is why a failed publish does not block activation.
    """
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sos store unavailable; please retry shortly",
        )
    return await sos.list_active(limit=limit)


@router.put(
    "/temple/info",
    response_model=TempleInfoResponse,
    summary="Update the temple information card",
    responses={
        400: {"description": "Nothing to update"},
        401: {"description": "Invalid or missing X-API-Key"},
        503: {"description": "ADMIN_API_KEY unset, or the store is unavailable"},
    },
)
async def update_temple_info(
    payload: TempleInfoUpdate,
    temple: Annotated[TempleService, Depends(get_temple_service)],
    db: DbSession,
    _: ApiKey,
) -> TempleInfoResponse:
    """Partial update — send only the fields that changed.

    Writes the row and drops the cached copy, so a timing corrected during the
    Wari is visible immediately rather than up to an hour later.
    """
    changes = payload.model_dump(exclude={"language"}, exclude_none=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide at least one of: title, timings, rituals, events, description",
        )
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="temple info store unavailable; please retry shortly",
        )

    return await temple.update(payload.language, changes)
