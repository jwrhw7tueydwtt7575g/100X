"""Conversation endpoints — what the frontend calls in place of `mockApi.ts`.

Both endpoints are thin: `LLMOrchestrator.process_message()` owns prompting,
tool calling, widget building and persistence.

⚠️ SHARED SESSION ID
The frontend ships a literal `session_id` of `"wariverse-session"`. That string
is the same on every install, so every *anonymous* pilgrim using this build
lands in one shared session: their transcripts interleave, and one person's
"yes" can confirm another's emergency.

Authenticated callers are safe — the key is scoped to their user id (see
`session_key()` in app/services/session_service.py). Anonymous ones are not.
The fix belongs in the client: generate a random id once per install and store
it, rather than hard-coding one. Until then, treat anonymous chat as shared.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.exceptions import RedisError

from app.config import settings
from app.data.i18n import t
from app.deps import get_orchestrator, get_session_service, get_sos_service
from app.models.schemas import (
    ConversationMessageRequest,
    ConversationMessageResponse,
    SosConfirmRequest,
)
from app.redis_client import get_redis
from app.security import OptionalToken
from app.services.llm_orchestrator import (
    ConversationHistory,
    LLMOrchestrator,
    is_affirmative,
    new_message_id,
)
from app.services.session_service import SessionService, session_key
from app.services.sos_service import SosService
from app.utils import now_utc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversation", tags=["conversation"])

RATE_PREFIX = "wv:chat:rate:"
RATE_LIMIT_MESSAGES = 30
RATE_LIMIT_WINDOW_SECONDS = 60


@router.post(
    "/message",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a message to the assistant",
    responses={429: {"description": "More than 30 messages in a minute"}},
)
async def post_message(
    payload: ConversationMessageRequest,
    request: Request,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    orchestrator: Annotated[LLMOrchestrator, Depends(get_orchestrator)],
    sos: Annotated[SosService, Depends(get_sos_service)],
    caller: OptionalToken = None,
) -> ConversationMessageResponse:
    user_id = caller.user_id if caller else None
    await _check_rate_limit(payload.session_id, user_id)

    # A bare "yes" right after an SOS prompt is a confirmation, not a new
    # query, and must not go near the model.
    if payload.session_id and is_affirmative(payload.message):
        state = await sessions.resolve(payload.session_id, user_id=user_id)
        if state.pending_sos:
            return await _activate(
                state=state,
                sessions=sessions,
                sos=sos,
                language=payload.language or state.language,
                user_text=payload.message,
                location=payload.location,
                request=request,
            )

    result = await orchestrator.process_message(
        session_id=payload.session_id,
        user_message=payload.message,
        language=payload.language,
        channel=payload.channel,
        location=payload.location,
        user_id=user_id,
        is_voice=payload.is_voice,
    )

    log.info(
        "conversation_message",
        session_id=result.session_id,
        message_id=result.message_id,
        authenticated=user_id is not None,
        response_time_ms=result.response_time_ms,
        request_id=getattr(request.state, "request_id", None),
    )
    return ConversationMessageResponse(**result.to_response())


@router.post(
    "/sos/confirm",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm the emergency raised in this session",
    responses={404: {"description": "No session with that id"}},
)
async def confirm_sos(
    payload: SosConfirmRequest,
    request: Request,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    sos: Annotated[SosService, Depends(get_sos_service)],
    caller: OptionalToken = None,
) -> ConversationMessageResponse:
    user_id = caller.user_id if caller else None
    state = await sessions.resolve(payload.session_id, user_id=user_id)
    language = payload.language or state.language

    if not payload.confirmed:
        await sessions.set_pending_sos(state, False)
        message = t("sos_cancelled", language)
        widget = {
            "type": "sos",
            "data": {
                "status": "FAILED",
                "message": message,
                "control_room_status": "CANCELLED",
                "timestamp": now_utc().isoformat(),
            },
        }
        await sessions.record_turn(
            state, "[sos_cancel]", message, intent="sos", widgets=[widget]
        )
        await _remember(state.session_id, "[sos_cancel]", message)
        return _respond(state, payload.session_id, language, message, [widget])

    return await _activate(
        state=state,
        sessions=sessions,
        sos=sos,
        language=language,
        user_text="[sos_confirm]",
        location=payload.location,
        request=request,
        emergency_type=payload.emergency_type,
        phone=payload.phone,
        description=payload.description,
    )


# --- helpers ----------------------------------------------------------------


def _respond(
    state, client_key: str | None, language: str, text: str, widgets: list[dict]
) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        session_id=client_key or str(state.session_id),
        message_id=new_message_id(),
        language=language,  # type: ignore[arg-type]
        response_text=text,
        widgets=widgets,  # type: ignore[arg-type]
    )


async def _remember(session_id, user_text: str, assistant_text: str) -> None:
    """Mirror a turn into the LLM replay history.

    These paths bypass the orchestrator, so without this the model would not
    know an emergency was just raised and would answer the next question as if
    nothing had happened.
    """
    await ConversationHistory(session_id).append(
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    )


async def _activate(
    *,
    state,
    sessions: SessionService,
    sos: SosService,
    language: str,
    user_text: str,
    location,
    request: Request,
    emergency_type: str = "other",
    phone: str | None = None,
    description: str | None = None,
) -> ConversationMessageResponse:
    """Dispatch the emergency and answer with an `sos` widget."""
    lat = location.lat if location else state.last_lat
    lon = location.lon if location else state.last_lon

    if lat is None or lon is None:
        message = t("sos_no_location", language, helpline=settings.emergency_helpline)
        widget = {
            "type": "sos",
            "data": {
                "status": "FAILED",
                "message": message,
                "control_room_status": "UNREACHABLE",
                "timestamp": now_utc().isoformat(),
            },
        }
        await sessions.record_turn(
            state, user_text, message, intent="sos", widgets=[widget]
        )
        await _remember(state.session_id, user_text, message)
        log.error(
            "sos_without_location",
            session_id=state.client_key or str(state.session_id),
            request_id=getattr(request.state, "request_id", None),
        )
        return _respond(state, state.client_key, language, message, [widget])

    event = await sos.dispatch(
        lat=lat,
        lon=lon,
        emergency_type=emergency_type,
        language=language,
        user_id=state.user_id,
        session_id=state.session_id,
        phone=phone,
        description=description,
    )
    await sessions.set_pending_sos(state, False)

    widget = {
        "type": "sos",
        "data": {
            "status": "ACTIVATED",
            "message": event.message,
            "control_room_status": "NOTIFIED",
            "timestamp": event.created_at.isoformat(),
        },
    }
    await sessions.record_turn(
        state, user_text, event.message, intent="sos", widgets=[widget]
    )
    await _remember(state.session_id, user_text, event.message)

    log.warning(
        "sos_activated",
        session_id=state.client_key or str(state.session_id),
        sos_id=str(event.sos_id),
        eta_minutes=event.eta_minutes,
        request_id=getattr(request.state, "request_id", None),
    )
    return _respond(state, state.client_key, language, event.message, [widget])


async def _check_rate_limit(client_key: str | None, user_id) -> None:
    """30 messages per minute per session.

    Fails **open** when Redis is down — unlike the OTP limiter, which fails
    closed. An OTP send costs money and enables SMS bombing; a chat message
    costs a model call, and refusing a pilgrim's question about a medical post
    because the cache blipped is the worse outcome.
    """
    if not client_key:
        return

    client = get_redis()
    if client is None:
        return

    key = f"{RATE_PREFIX}{session_key(client_key, user_id)}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    except (RedisError, OSError) as exc:
        log.warning("chat_rate_limit_unavailable", error=str(exc))
        return

    if count > RATE_LIMIT_MESSAGES:
        log.warning("chat_rate_limited", session_id=client_key, count=count)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"too many messages; limit is {RATE_LIMIT_MESSAGES} per minute. "
                f"For an emergency call {settings.emergency_helpline}."
            ),
        )
