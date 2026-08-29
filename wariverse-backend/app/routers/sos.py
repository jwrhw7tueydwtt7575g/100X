"""Emergency SOS.

`POST /trigger` is deliberately **unauthenticated**: a pilgrim in trouble who
never registered must still be able to call for help, and an emergency is not
the moment to discover your token expired. A bearer token, when present,
attributes the event so responders can call back.

The two status endpoints are operator-only and take the admin API key.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.deps import DbSession, Language, get_sos_service
from app.models.schemas import (
    SosEventAdmin,
    SosStatusUpdate,
    SosTriggerRequest,
    SosTriggerResponse,
)
from app.routers.admin import ApiKey
from app.security import OptionalToken
from app.services.sos_service import SosNotFoundError, SosService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sos", tags=["sos"])

SosId = Annotated[UUID, Path(description="Id returned by /api/sos/trigger.")]


@router.post(
    "/trigger",
    response_model=SosTriggerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Raise an emergency and alert the control room",
)
async def trigger_sos(
    payload: SosTriggerRequest,
    request: Request,
    sos: Annotated[SosService, Depends(get_sos_service)],
    language: Language,
    caller: OptionalToken = None,
) -> SosTriggerResponse:
    response = await sos.trigger(
        latitude=payload.latitude,
        longitude=payload.longitude,
        session_id=payload.session_id,
        channel=payload.channel,
        emergency_type=payload.emergency_type,
        language=payload.language or language,
        user_id=caller.user_id if caller else None,
        phone=payload.phone or (caller.phone_number if caller else None),
        description=payload.description,
        accuracy_m=payload.accuracy_m,
    )
    log.warning(
        "sos_request_completed",
        sos_id=str(response.sos_id),
        session_id=payload.session_id,
        channel=payload.channel,
        authenticated=caller is not None,
        request_id=getattr(request.state, "request_id", None),
    )
    return response


@router.post(
    "/{sos_id}/resolve",
    response_model=SosEventAdmin,
    summary="Mark an emergency resolved (operator only)",
    responses={
        401: {"description": "Invalid or missing X-API-Key"},
        404: {"description": "Unknown sos_id"},
        503: {"description": "ADMIN_API_KEY unset, or the store is unavailable"},
    },
)
async def resolve_sos(
    sos_id: SosId,
    sos: Annotated[SosService, Depends(get_sos_service)],
    db: DbSession,
    _: ApiKey,
    payload: SosStatusUpdate | None = None,
) -> SosEventAdmin:
    _require_store(db)
    try:
        return await sos.resolve(sos_id, payload.note if payload else None)
    except SosNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown sos_id: {sos_id}"
        ) from exc


@router.post(
    "/{sos_id}/update-status",
    response_model=SosEventAdmin,
    summary="Move an emergency between states (operator only)",
    responses={
        401: {"description": "Invalid or missing X-API-Key"},
        404: {"description": "Unknown sos_id"},
        503: {"description": "ADMIN_API_KEY unset, or the store is unavailable"},
    },
)
async def update_sos_status(
    payload: SosStatusUpdate,
    sos_id: SosId,
    sos: Annotated[SosService, Depends(get_sos_service)],
    db: DbSession,
    _: ApiKey,
) -> SosEventAdmin:
    _require_store(db)
    try:
        return await sos.set_status(sos_id, payload.status, payload.note)
    except SosNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown sos_id: {sos_id}"
        ) from exc


def _require_store(db) -> None:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sos store unavailable; please retry shortly",
        )
