"""In-app IVR: the same menu tree, driven by the app instead of a phone line.

No telephony provider. The app posts DTMF keypresses and recorded audio to
these endpoints; the backend runs the menu state machine, calls the same domain
services the chat assistant uses, and returns spoken audio as base64 MP3.

Mounted at `/api/ivr/session/*`, separate from the Twilio webhooks at
`/api/ivr/voice/*`, which are untouched.

Two things this channel has that a phone call does not: the app knows the
pilgrim's GPS, so "nearby facilities" is answerable without asking where they
are; and there is a screen, so a menu choice can return a widget alongside the
audio.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.config import settings
from app.deps import (
    DbSession,
    get_crowd_service,
    get_facility_service,
    get_orchestrator,
    get_session_service,
    get_sos_service,
    get_temple_service,
)
from app.models.schemas import (
    GeoPoint,
    IvrDtmfRequest,
    IvrOption,
    IvrResponse,
    IvrStartRequest,
)
from app.redis_client import get_redis
from app.security import OptionalToken
from app.services import ivr_state, stt
from app.services.crowd_service import CrowdService
from app.services.facility_service import FacilityService
from app.services.ivr_state import Transition
from app.services.llm_orchestrator import LLMOrchestrator
from app.services.session_service import SessionService
from app.services.sos_service import SosService
from app.services.temple_service import TempleService
from app.services.voice_service import VoiceService, voice_service
from app.utils import format_clock, humanize_age, now_utc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/ivr/session", tags=["ivr"])

DEFAULT_SESSION = "wariverse-session"


# --- endpoints --------------------------------------------------------------


@router.post(
    "/start",
    response_model=IvrResponse,
    summary="Open an IVR session and get the opening prompt",
)
async def start(
    payload: IvrStartRequest,
    request: Request,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    caller: OptionalToken = None,
) -> IvrResponse:
    state = await sessions.resolve(
        payload.session_id or DEFAULT_SESSION,
        user_id=caller.user_id if caller else None,
        language=payload.language,
        channel="ivr",
    )
    _remember_location(state, payload.latitude, payload.longitude)

    # A caller who already picked a language in the app skips the language menu.
    transition = (
        ivr_state.main_menu(payload.language, action="language_selected")
        if payload.language
        else ivr_state.start()
    )
    if payload.language:
        state.language = payload.language

    await _store(sessions, state, transition.state)
    log.info(
        "ivr_session_started",
        session_id=state.client_key,
        state=transition.state,
        language=state.language,
        request_id=getattr(request.state, "request_id", None),
    )
    return await _respond(state, transition)


@router.post(
    "/dtmf",
    response_model=IvrResponse,
    summary="Press a key and advance the menu",
)
async def dtmf(
    payload: IvrDtmfRequest,
    request: Request,
    db: DbSession,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    temple: Annotated[TempleService, Depends(get_temple_service)],
    facilities: Annotated[FacilityService, Depends(get_facility_service)],
    sos: Annotated[SosService, Depends(get_sos_service)],
    caller: OptionalToken = None,
) -> IvrResponse:
    # A client on a bad connection retries a keypress it never saw answered.
    # Without this, the retry presses the key again from the state the first
    # attempt already moved to — two menu levels for one tap, and in the
    # `sos_confirm` branch a "1" that means something quite different the
    # second time around.
    if replayed := await _replayed_turn(payload.session_id, payload.turn_id):
        log.info(
            "ivr_turn_replayed",
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            key=payload.key,
        )
        return replayed

    state = await sessions.resolve(
        payload.session_id,
        user_id=caller.user_id if caller else None,
        channel="ivr",
    )
    _remember_location(state, payload.latitude, payload.longitude)

    current = (state.ivr or {}).get("state", "language")
    transition = ivr_state.next_state(current, payload.key, state.language)

    if transition.action == "language_selected":
        state.language = ivr_state.LANGUAGE_KEYS.get(payload.key, state.language)

    widgets = await _perform(
        transition,
        state=state,
        sessions=sessions,
        crowd=crowd,
        temple=temple,
        facilities=facilities,
        sos=sos,
    )

    await _store(sessions, state, transition.state)
    log.info(
        "ivr_dtmf",
        session_id=state.client_key,
        key=payload.key,
        from_state=current,
        to_state=transition.state,
        action=transition.action,
        language=state.language,
        request_id=getattr(request.state, "request_id", None),
    )
    response = await _respond(state, transition, widgets)
    await _remember_turn(payload.session_id, payload.turn_id, response)
    return response


@router.post(
    "/voice",
    response_model=IvrResponse,
    summary="Speak a question and hear the answer",
    responses={
        413: {"description": "Audio too large"},
        415: {"description": "Unsupported audio format"},
        503: {"description": "No transcription provider available"},
    },
)
async def voice(
    request: Request,
    sessions: Annotated[SessionService, Depends(get_session_service)],
    orchestrator: Annotated[LLMOrchestrator, Depends(get_orchestrator)],
    file: Annotated[UploadFile, File(description="WAV, AAC/m4a or WebM")],
    session_id: Annotated[str, Form()] = DEFAULT_SESSION,
    turn_id: Annotated[str | None, Form(max_length=64)] = None,
    caller: OptionalToken = None,
) -> IvrResponse:
    """Transcribe, answer through the orchestrator, and speak the reply back."""
    # A retried upload is far more expensive than a retried keypress: it pays
    # for transcription and a model turn again, and the pilgrim hears a second,
    # differently-worded answer to the question they asked once.
    if replayed := await _replayed_turn(session_id, turn_id):
        log.info("ivr_turn_replayed", session_id=session_id, turn_id=turn_id,
                 kind="voice")
        return replayed

    state = await sessions.resolve(
        session_id, user_id=caller.user_id if caller else None, channel="ivr"
    )

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in stt.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported audio type: {content_type or 'unknown'}",
        )

    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the audio file is empty"
        )
    if len(audio) > settings.voice_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"audio is {len(audio) // 1024} KB; the limit is "
                f"{settings.voice_max_upload_bytes // 1024 // 1024} MB"
            ),
        )

    try:
        heard = await voice_service.transcribe(audio, content_type, state.language)
    except stt.TranscriptionError as exc:
        log.error("ivr_transcription_failed", session_id=session_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="could not understand the recording; please use the menu keys",
        ) from exc

    # Speaking in a language sets the call's language, so the answer comes back
    # in the language the pilgrim actually used.
    if heard.language in ("mr", "hi", "en"):
        state.language = heard.language

    result = await orchestrator.process_message(
        session_id=state.client_key,
        user_message=heard.transcript,
        language=state.language,
        channel="ivr",
        location=_point(state),
        user_id=state.user_id,
        is_voice=True,
        # Unlike the phone line, the app has a screen — show the cards while
        # the answer plays.
        with_widgets=True,
    )

    await _store(sessions, state, "speech")
    log.info(
        "ivr_voice_turn",
        session_id=state.client_key,
        provider=heard.provider,
        language=heard.language,
        confidence=heard.confidence,
        characters=len(heard.transcript),
        request_id=getattr(request.state, "request_id", None),
    )

    transition = Transition(
        state="speech",
        action="enter_speech",
        options=[IvrOptionBack(state.language)],
        text=result.response_text,
    )
    response = await _respond(state, transition, widgets=result.widgets)
    await _remember_turn(session_id, turn_id, response)
    return response


# --- menu actions -----------------------------------------------------------


async def _perform(
    transition: Transition,
    *,
    state,
    sessions: SessionService,
    crowd: CrowdService,
    temple: TempleService,
    facilities: FacilityService,
    sos: SosService,
) -> list[dict[str, Any]]:
    """Carry out the action a transition asked for, filling in its prompt."""
    language = state.language
    widgets: list[dict[str, Any]] = []

    if transition.action == "crowd_summary":
        transition.text, widgets = await _crowd(crowd, language)

    elif transition.action == "temple_info":
        transition.text, widgets = await _temple(temple, crowd, language)

    elif transition.action == "nearby_seva":
        transition.text, widgets = await _nearby(facilities, state, language)

    elif transition.action == "sos_dispatch":
        transition.text, widgets = await _sos(sos, sessions, state, language)

    elif transition.action == "escalate":
        record = await sessions.escalate(state, "IVR menu escalation")
        transition.text = t_escalation(language, record)

    # `replay`, `invalid`, `greet`, `language_selected`, `enter_speech` and
    # `goodbye` carry their own static text from the state machine.
    if transition.text and transition.action in (
        "crowd_summary",
        "temple_info",
        "nearby_seva",
        "sos_dispatch",
    ):
        # Always re-offer the menu, so a pilgrim is never left with an answer
        # and no way forward.
        transition.text = f"{transition.text} {ivr_state.menu_prompt(language)}"

    return widgets


async def _crowd(crowd: CrowdService, language: str) -> tuple[str, list[dict]]:
    readings = await crowd.read_all(language)
    busiest = max(readings, key=lambda r: r.density)
    quietest = min(readings, key=lambda r: r.density)

    text = {
        "mr": f"सध्या {busiest.zone_name} येथे सर्वाधिक गर्दी आहे. "
              f"{quietest.zone_name} येथे तुलनेने कमी गर्दी आहे.",
        "hi": f"अभी {busiest.zone_name} पर सबसे ज़्यादा भीड़ है। "
              f"{quietest.zone_name} पर अपेक्षाकृत कम भीड़ है।",
        "en": f"{busiest.zone_name} is the busiest right now. "
              f"{quietest.zone_name} is comparatively clear.",
    }.get(language)
    if text is None:
        text = (
            f"{busiest.zone_name} is the busiest right now. "
            f"{quietest.zone_name} is comparatively clear."
        )

    widgets = [
        {
            "type": "crowd_density",
            "data": {
                "zone_id": reading.zone_id,
                "zone_name": reading.zone_name,
                "density": reading.density,
                "status": reading.status,
                "latitude": reading.latitude,
                "longitude": reading.longitude,
                "updated_at": humanize_age(reading.recorded_at, language),
            },
        }
        for reading in readings
    ]
    return text, widgets


async def _temple(
    temple: TempleService, crowd: CrowdService, language: str
) -> tuple[str, list[dict]]:
    info = await temple.get(language)
    try:
        queue = await crowd.read_zone("temple-main", language)
        wait = queue.wait_minutes
    except Exception:  # noqa: BLE001 — timings still answer the question
        wait = None

    spoken = {
        "mr": f"{info.title}. दर्शन वेळ {info.timings}.",
        "hi": f"{info.title}. दर्शन समय {info.timings}.",
        "en": f"{info.title}. Darshan timings are {info.timings}.",
    }.get(language, f"{info.title}. Darshan timings are {info.timings}.")

    if wait:
        spoken += {
            "mr": f" रांगेत अंदाजे {wait} मिनिटे लागतील.",
            "hi": f" कतार में लगभग {wait} मिनट लगेंगे।",
            "en": f" The queue is running about {wait} minutes.",
        }.get(language, f" The queue is running about {wait} minutes.")

    return spoken, [{"type": "temple_info", "data": info.model_dump()}]


async def _nearby(
    facilities: FacilityService, state, language: str
) -> tuple[str, list[dict]]:
    point = _point(state)
    if point is None:
        return (
            {
                "mr": "आपले ठिकाण माहीत नाही. कृपया अ‍ॅपमध्ये लोकेशन सुरू करा.",
                "hi": "आपका स्थान ज्ञात नहीं है। कृपया ऐप में लोकेशन चालू करें।",
                "en": "I do not know where you are. Please turn on location in the app.",
            }.get(language, "I do not know where you are."),
            [],
        )

    found = await facilities.nearby(
        point.lat,
        point.lon,
        radius_m=2000,
        limit=3,
        language=language,
        # Same filter `/api/facilities/nearby` applies: without it a lost &
        # found desk or police post outranks the water point someone asked for.
        facility_types=list(settings.facility_categories),
    )
    if not found:
        return (
            {
                "mr": "जवळपास नोंदवलेली सुविधा सापडली नाही.",
                "hi": "आसपास कोई दर्ज सुविधा नहीं मिली।",
                "en": "I could not find a registered facility nearby.",
            }.get(language, "I could not find a registered facility nearby."),
            [],
        )

    nearest = found[0]
    seva = " " + {
        "mr": "ही मोफत सेवा आहे.",
        "hi": "यह नि:शुल्क सेवा है।",
        "en": "This is a free community seva.",
    }.get(language, "This is a free community seva.") if nearest.is_seva else ""

    spoken = {
        "mr": f"सर्वात जवळ {nearest.name}, {nearest.distance} अंतरावर.{seva}",
        "hi": f"सबसे नज़दीक {nearest.name}, {nearest.distance} दूर।{seva}",
        "en": f"The nearest is {nearest.name}, {nearest.distance} away.{seva}",
    }.get(language, f"The nearest is {nearest.name}, {nearest.distance} away.{seva}")

    return spoken, [
        {"type": "nearby_facility", "data": f.model_dump()} for f in found
    ]


async def _sos(
    sos: SosService, sessions: SessionService, state, language: str
) -> tuple[str, list[dict]]:
    point = _point(state)
    event = await sos.trigger(
        latitude=point.lat if point else None,
        longitude=point.lon if point else None,
        session_id=state.client_key,
        channel="ivr",
        emergency_type="other",
        language=language,
        user_id=state.user_id,
        description="Raised from the in-app IVR menu",
    )
    await sessions.set_pending_sos(state, False)

    log.warning(
        "ivr_menu_sos",
        session_id=state.client_key,
        sos_id=str(event.sos_id),
        located=point is not None,
    )
    widget = {
        "type": "sos",
        "data": {
            "status": event.status,
            "message": event.message,
            "control_room_status": event.control_room_status,
            "timestamp": event.timestamp,
        },
    }
    return event.message, [widget]


def t_escalation(language: str, record: dict) -> str:
    from app.data.i18n import t

    return t("escalation_waiting", language, helpline=settings.wari_control_room)


# --- turn replay ------------------------------------------------------------
#
# Makes a keypress and a voice upload safely retryable. The client sends a
# `turn_id` it generates once per user action and reuses across retries; the
# first request to complete stores its answer under that id, and any repeat
# gets the stored answer back instead of being applied to the menu again.
#
# Deliberately keyed on the id the *client* chose rather than on the request
# body: two genuine presses of the same key are different turns and must both
# take effect, which a body hash would collapse into one.


def _turn_key(session_id: str, turn_id: str) -> str:
    return f"ivr:turn:{session_id}:{turn_id}"


async def _replayed_turn(session_id: str, turn_id: str | None) -> IvrResponse | None:
    """The stored answer for this turn, if it has already been answered."""
    if not turn_id:
        return None

    client = get_redis()
    if client is None:
        # No store, no replay protection. Degrading to at-most-once would mean
        # dropping the keypress entirely, which is worse than applying it twice.
        return None

    try:
        raw = await client.get(_turn_key(session_id, turn_id))
    except (RedisError, OSError) as exc:
        log.warning("ivr_turn_replay_read_failed", error=str(exc))
        return None

    if not raw:
        return None
    try:
        return IvrResponse.model_validate_json(raw)
    except ValidationError:
        # Written by an older build with a different shape. Treat it as absent
        # and let the turn run normally.
        log.warning("ivr_turn_replay_corrupt", session_id=session_id)
        return None


async def _remember_turn(
    session_id: str, turn_id: str | None, response: IvrResponse
) -> None:
    if not turn_id:
        return

    client = get_redis()
    if client is None:
        return

    try:
        await client.set(
            _turn_key(session_id, turn_id),
            response.model_dump_json(),
            ex=settings.ivr_turn_replay_ttl_seconds,
        )
    except (RedisError, OSError) as exc:
        # The turn itself succeeded; losing the receipt only means a retry would
        # be applied twice, which is the behaviour we had before.
        log.warning("ivr_turn_replay_write_failed", error=str(exc))


# --- helpers ----------------------------------------------------------------


def IvrOptionBack(language: str):  # noqa: N802 — reads as a constructor at the call site
    label = {"mr": "मेनूवर परत", "hi": "मेन्यू पर वापस"}.get(language, "Back to menu")
    return ivr_state.Option(key="0", label=label)


def _point(state) -> GeoPoint | None:
    if state.last_lat is None or state.last_lon is None:
        return None
    return GeoPoint(lat=state.last_lat, lon=state.last_lon)


def _remember_location(state, latitude: float | None, longitude: float | None) -> None:
    if latitude is not None and longitude is not None:
        state.last_lat, state.last_lon = latitude, longitude


async def _store(sessions: SessionService, state, ivr_state_name: str) -> None:
    state.ivr = {"state": ivr_state_name, "at": now_utc().isoformat()}
    await sessions.save(state)
    await sessions.ensure_row(state)
    # Through to Postgres as well: a stale `sos_confirm` surviving a Redis
    # flush would turn the caller's next "1" into an unintended dispatch.
    await sessions.sync_context(state)


async def _respond(
    state, transition: Transition, widgets: list[dict[str, Any]] | None = None
) -> IvrResponse:
    spoken = await voice_service.speak(transition.text, state.language)
    return IvrResponse(
        session_id=state.client_key or str(state.session_id),
        state=transition.state,  # type: ignore[arg-type]
        language=state.language,  # type: ignore[arg-type]
        prompt=transition.text,
        audio_base64=spoken.audio_base64,
        options=[IvrOption(key=o.key, label=o.label) for o in transition.options],
        widgets=widgets or [],  # type: ignore[arg-type]
        ends_session=transition.ends_session,
    )
