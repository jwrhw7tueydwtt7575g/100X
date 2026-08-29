"""Conversational endpoints — the assistant the pilgrim actually talks to."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status

from app.config import settings
from app.data.i18n import t
from app.deps import get_orchestrator, get_session_service, get_sos_service
from app.models.schemas import (
    ConversationMessageRequest,
    ConversationMessageResponse,
    SosConfirmRequest,
    SosConfirmResponse,
)
from app.services.llm_orchestrator import LLMOrchestrator, is_affirmative
from app.services.session_service import SessionService
from app.services.sos_service import SosService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversation", tags=["conversation"])


@router.post(
    "/message",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a message to the assistant",
)
async def post_message(
    payload: ConversationMessageRequest,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    orchestrator: Annotated[LLMOrchestrator, Depends(get_orchestrator)],
    sos: Annotated[SosService, Depends(get_sos_service)],
) -> ConversationMessageResponse:
    state = await sessions.get_or_create(
        session_id=payload.session_id, user_id=payload.user_id, language=payload.language
    )
    if payload.location is not None:
        state.last_lat = payload.location.lat
        state.last_lon = payload.location.lon

    # A bare "yes" right after an SOS prompt is a confirmation, not a new query.
    if state.pending_sos and is_affirmative(payload.text):
        return await _confirm_from_chat(payload, state, sessions, sos)

    result = await orchestrator.respond(
        payload.text, state, language=payload.language, location=payload.location
    )
    state.language = result.language

    if result.requires_sos_confirmation:
        await sessions.set_pending_sos(state, True)
    elif state.pending_sos:
        # Any other message clears a stale prompt so "yes" can't fire later.
        await sessions.set_pending_sos(state, False)

    await sessions.record_turn(
        state,
        payload.text,
        result.reply,
        intent=result.intent,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        model=result.model,
    )

    log.info(
        "conversation_turn",
        session_id=str(state.session_id),
        intent=result.intent,
        language=result.language,
        source=result.source,
        latency_ms=result.latency_ms,
    )

    return ConversationMessageResponse(
        session_id=state.session_id,
        reply=result.reply,
        language=result.language,  # type: ignore[arg-type]
        intent=result.intent,  # type: ignore[arg-type]
        confidence=result.confidence,
        actions=result.actions,
        data=result.data,
        requires_sos_confirmation=result.requires_sos_confirmation,
        source=result.source,  # type: ignore[arg-type]
        latency_ms=result.latency_ms,
    )


@router.post(
    "/sos/confirm",
    response_model=SosConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm or cancel an emergency raised in chat",
)
async def confirm_sos(
    payload: SosConfirmRequest,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    sos: Annotated[SosService, Depends(get_sos_service)],
) -> SosConfirmResponse:
    state = await sessions.get_or_create(
        session_id=payload.session_id, user_id=payload.user_id
    )
    language = state.language

    if not payload.confirmed:
        await sessions.set_pending_sos(state, False)
        message = t("sos_cancelled", language)
        await sessions.record_turn(state, "[sos_confirm:false]", message, intent="sos")
        return SosConfirmResponse(
            session_id=state.session_id, confirmed=False, message=message,
            language=language,  # type: ignore[arg-type]
        )

    lat, lon, accuracy = _resolve_location(payload, state)
    if lat is None or lon is None:
        message = t("sos_no_location", language, helpline=settings.emergency_helpline)
        return SosConfirmResponse(
            session_id=state.session_id, confirmed=True, message=message,
            language=language,  # type: ignore[arg-type]
        )

    event = await sos.dispatch(
        lat=lat,
        lon=lon,
        emergency_type=payload.emergency_type,
        language=language,
        user_id=payload.user_id or state.user_id,
        session_id=state.session_id,
        phone=payload.phone,
        description=payload.description,
        accuracy_m=accuracy,
    )

    await sessions.set_pending_sos(state, False)
    await sessions.record_turn(state, "[sos_confirm:true]", event.message, intent="sos")

    return SosConfirmResponse(
        session_id=state.session_id,
        confirmed=True,
        message=event.message,
        language=language,  # type: ignore[arg-type]
        sos=event,
    )


# --- helpers ----------------------------------------------------------------


def _resolve_location(payload, state) -> tuple[float | None, float | None, float | None]:
    if payload.location is not None:
        return payload.location.lat, payload.location.lon, payload.location.accuracy_m
    return state.last_lat, state.last_lon, None


async def _confirm_from_chat(
    payload: ConversationMessageRequest,
    state,
    sessions: SessionService,
    sos: SosService,
) -> ConversationMessageResponse:
    """Handle "yes" following an SOS prompt, without a second round trip."""
    lat = payload.location.lat if payload.location else state.last_lat
    lon = payload.location.lon if payload.location else state.last_lon
    language = payload.language or state.language

    if lat is None or lon is None:
        reply = t("sos_no_location", language, helpline=settings.emergency_helpline)
        await sessions.record_turn(state, payload.text, reply, intent="sos")
        return ConversationMessageResponse(
            session_id=state.session_id,
            reply=reply,
            language=language,  # type: ignore[arg-type]
            intent="sos",
            confidence=0.95,
            requires_sos_confirmation=True,
            latency_ms=0,
        )

    event = await sos.dispatch(
        lat=lat,
        lon=lon,
        language=language,
        user_id=state.user_id,
        session_id=state.session_id,
    )
    await sessions.set_pending_sos(state, False)
    await sessions.record_turn(state, payload.text, event.message, intent="sos")

    return ConversationMessageResponse(
        session_id=state.session_id,
        reply=event.message,
        language=language,  # type: ignore[arg-type]
        intent="sos",
        confidence=0.95,
        data={"sos": event.model_dump(mode="json")},
        requires_sos_confirmation=False,
        latency_ms=0,
    )
