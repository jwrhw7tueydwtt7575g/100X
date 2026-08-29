"""Direct SOS trigger — the panic button in the app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.deps import Language, get_sos_service
from app.models.schemas import SosEventResponse, SosTriggerRequest
from app.security import TokenPayload, get_optional_token
from app.services.sos_service import SosService

router = APIRouter(prefix="/sos", tags=["sos"])


@router.post(
    "/trigger",
    response_model=SosEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Raise an emergency and dispatch help to a coordinate",
)
async def trigger_sos(
    payload: SosTriggerRequest,
    sos: Annotated[SosService, Depends(get_sos_service)],
    language: Language,
    caller: Annotated[TokenPayload | None, Depends(get_optional_token)] = None,
) -> SosEventResponse:
    # Deliberately unauthenticated: an unregistered pilgrim in trouble must
    # still be able to call for help. A token, when present, attributes the
    # event to the user so responders can call them back.
    return await sos.dispatch(
        lat=payload.lat,
        lon=payload.lon,
        emergency_type=payload.emergency_type,
        language=payload.language or language,
        user_id=payload.user_id or (caller.user_id if caller else None),
        session_id=payload.session_id or (caller.session_id if caller else None),
        phone=payload.phone or (caller.phone_number if caller else None),
        description=payload.description,
        accuracy_m=payload.accuracy_m,
    )
