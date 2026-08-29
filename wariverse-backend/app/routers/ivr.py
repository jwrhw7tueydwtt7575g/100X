"""Twilio Voice IVR — the same assistant, reached by dialling the helpline.

A pilgrim with a feature phone and no data gets the tools the app users get.
Every webhook is signature-checked (see `app/services/twilio_signature.py`);
the endpoints are public URLs and digit 9 dispatches responders.

Call shape:

    answer → language (DTMF 1/2/3) → transcribe ⟲ → status
                                        ↑ ↓
                                     dtmf 1/2/3/9/0

`CallSid` is the session id, so the orchestrator sees one continuous
conversation across the call and the transcript lands in `messages` under a
session with `channel="ivr"`.

Transcripts are written **per turn**, not at call end: a dropped call would
otherwise lose the whole conversation, including an emergency. The status
webhook records call metadata and closes the session.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response

from app.config import settings
from app.data.i18n import t
from app.data.reference import ZONES_BY_ID
from app.deps import get_orchestrator, get_session_service, get_sos_service
from app.models.schemas import GeoPoint
from app.services.llm_orchestrator import LLMOrchestrator, zone_from_text
from app.services.session_service import SessionService
from app.services.sos_service import SosService
from app.services.twilio_signature import verify_twilio_request
from app.services.twiml import VoiceResponse
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/ivr/voice", tags=["ivr"])

TwilioForm = Annotated[dict[str, str], Depends(verify_twilio_request)]

# Which digit maps to which language on the opening menu.
DIGIT_LANGUAGES = {"1": "mr", "2": "hi", "3": "en"}

# Canned questions for the DTMF shortcuts. They go through the orchestrator so
# a phone caller gets the same grounded answers as an app user.
DIGIT_QUESTIONS = {
    "1": "When will it be less crowded at the temple?",
    "2": "How crowded is the temple right now?",
    "3": "Where is the nearest medical post?",
}

# Where the dashboard hears about a caller asking for a human.
ESCALATION_CHANNEL = "wv:escalation:requests"


def twiml(response: VoiceResponse) -> Response:
    return Response(content=response.to_xml(), media_type="application/xml")


# --- language selection -----------------------------------------------------


@router.post("/answer", summary="Twilio webhook: call answered")
async def answer(form: TwilioForm) -> Response:
    """Greet in all three languages and ask which one to continue in."""
    call_sid = form.get("CallSid", "")
    log.info("ivr_call_answered", call_sid=call_sid, caller=_mask(form.get("From")))

    response = VoiceResponse()
    gather = response.gather(
        _url("/language"), input_types="dtmf", num_digits=1, timeout=8
    )
    # Each line in its own voice, so a Marathi speaker hears Marathi first.
    for language in ("mr", "hi", "en"):
        gather.say(t("ivr_language_prompt", language), language)

    # No key pressed: Marathi is the Wari's first language.
    response.redirect(_url("/language?Digits=1"))
    return twiml(response)


@router.post("/language", summary="Twilio webhook: language chosen")
async def choose_language(
    request: Request,
    form: TwilioForm,
    sessions: Annotated[SessionService, Depends(get_session_service)],
) -> Response:
    call_sid = form.get("CallSid", "")
    digit = form.get("Digits") or request.query_params.get("Digits") or "1"
    language = DIGIT_LANGUAGES.get(digit, settings.default_language)

    # The call's language lives on its session, keyed by CallSid.
    state = await sessions.resolve(call_sid, language=language, channel="ivr")
    state.language = language
    await sessions.save(state)
    await sessions.ensure_row(state)

    log.info("ivr_language_selected", call_sid=call_sid, language=language)

    response = VoiceResponse()
    gather = _listen(response, language)
    gather.say(t("ivr_ask_question", language), language)
    response.redirect(_url("/transcribe"))
    return twiml(response)


# --- the conversation loop --------------------------------------------------


@router.post("/transcribe", summary="Twilio webhook: speech result")
async def transcribe(
    form: TwilioForm,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    orchestrator: Annotated[LLMOrchestrator, Depends(get_orchestrator)],
    sos: Annotated[SosService, Depends(get_sos_service)],
) -> Response:
    call_sid = form.get("CallSid", "")
    state = await sessions.resolve(call_sid, channel="ivr")
    language = state.language

    # The gather accepts speech *and* digits, so a caller can press 9 mid-
    # sentence. Digits win: they are unambiguous and one of them is an SOS.
    if digits := form.get("Digits"):
        return await _handle_digit(
            digits, form, state, sessions, orchestrator, sos, language
        )

    speech = (form.get("SpeechResult") or "").strip()
    if not speech:
        response = VoiceResponse()
        gather = _listen(response, language)
        gather.say(t("ivr_no_input", language), language)
        response.say(t("ivr_goodbye", language), language)
        response.hangup()
        return twiml(response)

    # A caller cannot share GPS, but they can say where they are.
    await _remember_spoken_location(speech, state, sessions)

    log.info(
        "ivr_speech_received",
        call_sid=call_sid,
        language=language,
        confidence=form.get("Confidence"),
        characters=len(speech),
    )

    reply = await _ask(orchestrator, call_sid, speech, language, state)

    response = VoiceResponse()
    response.say(reply, language)
    gather = _listen(response, language)
    gather.say(t("ivr_anything_else", language), language)
    response.say(t("ivr_goodbye", language), language)
    response.hangup()
    return twiml(response)


@router.post("/dtmf", summary="Twilio webhook: DTMF fallback")
async def dtmf(
    form: TwilioForm,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    orchestrator: Annotated[LLMOrchestrator, Depends(get_orchestrator)],
    sos: Annotated[SosService, Depends(get_sos_service)],
) -> Response:
    call_sid = form.get("CallSid", "")
    state = await sessions.resolve(call_sid, channel="ivr")
    return await _handle_digit(
        form.get("Digits", ""), form, state, sessions, orchestrator, sos, state.language
    )


# --- call completion --------------------------------------------------------


@router.post("/status", summary="Twilio webhook: call status")
async def call_status(
    form: TwilioForm,
    sessions: Annotated[SessionService, Depends(get_session_service)],
) -> Response:
    """Record how the call ended.

    The transcript is already in `messages` — each turn is written as it
    happens, so a call that drops mid-sentence still leaves everything said up
    to that point. This closes the session and stores call metadata on it.
    """
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "unknown")

    if status in ("completed", "busy", "failed", "no-answer", "canceled"):
        state = await sessions.resolve(call_sid, channel="ivr")
        turns = len(state.messages) // 2
        await sessions.finish_call(
            state,
            {
                "call_status": status,
                "duration_seconds": _int(form.get("CallDuration")),
                "from": form.get("From"),
                "turns": turns,
            },
        )
        log.info(
            "ivr_call_completed",
            call_sid=call_sid,
            call_status=status,
            duration_seconds=_int(form.get("CallDuration")),
            turns=turns,
            language=state.language,
        )

    # Twilio ignores TwiML here, but an empty document is the correct reply.
    return twiml(VoiceResponse())


# --- digit handling ---------------------------------------------------------


async def _handle_digit(
    digit: str,
    form: dict[str, str],
    state,
    sessions: SessionService,
    orchestrator: LLMOrchestrator,
    sos: SosService,
    language: str,
) -> Response:
    call_sid = form.get("CallSid", "")
    log.info("ivr_digit", call_sid=call_sid, digit=digit, language=language)

    if digit == "9":
        return await _emergency(form, state, sessions, sos, language)
    if digit == "0":
        return await _escalate(form, state, sessions, language)

    question = DIGIT_QUESTIONS.get(digit)
    if question is None:
        response = VoiceResponse()
        gather = _listen(response, language)
        gather.say(t("ivr_ask_question", language), language)
        response.hangup()
        return twiml(response)

    if digit == "3" and state.last_lat is None:
        # "Nearest" is meaningless without knowing where they are, and guessing
        # would send someone the wrong way.
        response = VoiceResponse()
        gather = _listen(response, language)
        gather.say(t("ivr_where_are_you", language), language)
        response.hangup()
        return twiml(response)

    reply = await _ask(orchestrator, call_sid, question, language, state)

    response = VoiceResponse()
    response.say(reply, language)
    gather = _listen(response, language)
    gather.say(t("ivr_anything_else", language), language)
    response.say(t("ivr_goodbye", language), language)
    response.hangup()
    return twiml(response)


async def _emergency(
    form: dict[str, str],
    state,
    sessions: SessionService,
    sos: SosService,
    language: str,
) -> Response:
    """Digit 9: dispatch immediately, no model in the loop.

    A caller pressing 9 has already decided. Routing that through an LLM would
    add latency and a chance of misreading it.
    """
    call_sid = form.get("CallSid", "")
    caller = form.get("From")

    event = await sos.trigger(
        latitude=state.last_lat,
        longitude=state.last_lon,
        session_id=call_sid,
        channel="ivr",
        emergency_type="other",
        language=language,
        phone=caller,
        description=f"IVR emergency key from {caller or 'unknown number'}",
    )
    await sessions.record_turn(
        state, "[ivr:9]", event.message, intent="sos", is_voice=True
    )
    log.warning(
        "ivr_sos_triggered",
        call_sid=call_sid,
        sos_id=str(event.sos_id),
        caller=_mask(caller),
        located=state.last_lat is not None,
    )

    response = VoiceResponse()
    response.say(event.message, language)
    # Stay on the line: a caller in an emergency should not be cut off.
    gather = _listen(response, language)
    gather.say(t("ivr_anything_else", language), language)
    response.hangup()
    return twiml(response)


async def _escalate(
    form: dict[str, str], state, sessions: SessionService, language: str
) -> Response:
    """Digit 0: hand the call to a volunteer and play hold music."""
    call_sid = form.get("CallSid", "")
    record = await sessions.escalate(state, "IVR caller pressed 0")
    await _notify_dashboard(call_sid, form.get("From"), language)
    await sessions.record_turn(
        state, "[ivr:0]", t("ivr_escalation_hold", language), intent="escalate",
        is_voice=True,
    )

    log.warning(
        "ivr_escalation", call_sid=call_sid, caller=_mask(form.get("From")),
        status=record["status"],
    )

    response = VoiceResponse()
    response.say(t("ivr_escalation_hold", language), language)
    # Hold music while a volunteer picks up. Twilio needs something to play or
    # the call ends; looping keeps the line open.
    response.play(settings.ivr_hold_music_url, loop=10)
    response.say(t("ivr_goodbye", language), language)
    response.hangup()
    return twiml(response)


# --- helpers ----------------------------------------------------------------


def _listen(response: VoiceResponse, language: str):
    """Open-ended listen: speech or a digit, with the configured silence wait."""
    return response.gather(
        _url("/transcribe"),
        input_types="speech dtmf",
        speech_timeout=settings.ivr_speech_timeout_seconds,
        timeout=settings.ivr_speech_timeout_seconds,
        language=language,
    )


async def _ask(
    orchestrator: LLMOrchestrator,
    call_sid: str,
    message: str,
    language: str,
    state,
) -> str:
    """One orchestrator turn, with the failure mode a phone line needs."""
    try:
        result = await orchestrator.process_message(
            session_id=call_sid,
            user_message=message,
            language=language,
            channel="ivr",
            location=(
                GeoPoint(lat=state.last_lat, lon=state.last_lon)
                if state.last_lat is not None and state.last_lon is not None
                else None
            ),
            is_voice=True,
        )
        return result.response_text
    except Exception:  # noqa: BLE001 — the caller must hear something
        log.exception("ivr_orchestrator_failed", call_sid=call_sid)
        return t("ivr_error", language)


async def _remember_spoken_location(speech: str, state, sessions: SessionService) -> None:
    """Pin the caller to a zone if they named one.

    An IVR caller has no GPS, but "I am near Gate 2" is just as good — and it
    is the only way `nearest facility` can mean anything on this channel.
    """
    zone_id = zone_from_text(speech)
    if zone_id is None:
        return
    zone = ZONES_BY_ID[zone_id]
    if (state.last_lat, state.last_lon) == (zone["lat"], zone["lon"]):
        return
    state.last_lat, state.last_lon = zone["lat"], zone["lon"]
    await sessions.save(state)
    log.info("ivr_location_from_speech", zone_id=zone_id)


async def _notify_dashboard(call_sid: str, caller: str | None, language: str) -> None:
    import json

    from redis.exceptions import RedisError

    client = get_redis()
    if client is None:
        log.error("ivr_escalation_publish_unavailable", call_sid=call_sid)
        return
    try:
        await client.publish(
            ESCALATION_CHANNEL,
            json.dumps(
                {
                    "call_sid": call_sid,
                    "caller": caller,
                    "language": language,
                    "channel": "ivr",
                },
                ensure_ascii=False,
            ),
        )
    except (RedisError, OSError) as exc:
        log.error("ivr_escalation_publish_failed", call_sid=call_sid, error=str(exc))


def _url(path: str) -> str:
    base = (settings.ivr_public_base_url or "").rstrip("/")
    return f"{base}{settings.api_prefix}/ivr/voice{path}"


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mask(number: str | None) -> str:
    if not number or len(number) <= 6:
        return "****"
    return f"{number[:3]}****{number[-3:]}"
