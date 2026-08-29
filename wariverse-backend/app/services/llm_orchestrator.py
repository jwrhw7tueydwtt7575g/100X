"""The conversational brain: prompt → tools → grounded reply + widgets.

Flow for one turn:

1. Load the last 10 turns from Redis (`session:{session_id}:history`).
2. Build a system prompt for the pilgrim's language and channel.
3. Call gpt-4o with the eight tools below exposed as function schemas.
4. Execute any tool the model asks for, feed the results back, get the reply.
5. Map each tool result to a widget matching the frontend's `domain.ts`.
6. Persist both messages and update the Redis history.

Three rules the implementation holds to:

* **Facts come from tools, never from the model.** Crowd numbers, distances and
  timings are computed by the domain services; the prompt forbids inventing
  them. A model that answers a crowd question without calling a tool is
  answering from imagination, and pilgrims route themselves on that answer.
* **Safety wording is deterministic.** An SOS is created as PENDING and the
  widget says `CONFIRMATION_REQUIRED`; only an explicit confirmation activates
  it. The model can raise an emergency, it cannot dispatch one.
* **It degrades rather than disappearing.** With no API key, a timeout or an
  API error, a keyword router runs the same tools and returns the same widget
  shapes, flagged `source="rules"`.

NOTE ON CASING: the envelope is snake_case like the rest of the API, but widget
`data` payloads are camelCase — they are consumed directly by the React Native
types in `domain.ts`, and renaming them at the boundary would be worse than the
inconsistency.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.data.reference import ZONES_BY_ID, localized_name
from app.data.temple import TEMPLE_CONTACT, temple_content
from app.models.schemas import GeoPoint
from app.redis_client import get_redis
from app.services.crowd_service import CrowdService, ZoneNotFoundError
from app.services.facility_service import FacilityService
from app.services.route_service import (
    TEMPLE_LAT,
    TEMPLE_LON,
    DestinationNotFoundError,
    RouteService,
)
from app.services.session_service import SessionService, SessionState
from app.services.sos_service import SosService
from app.services.temple_service import TempleService
from app.utils import format_clock, humanize_age, now_utc

log = structlog.get_logger(__name__)

HISTORY_KEY = "session:{session_id}:history"
MAX_HISTORY_MESSAGES = 10
MAX_TOOL_ROUNDS = 3
DEFAULT_ZONE = "temple-main"

# Facility categories are now stored verbatim as `facility_type`, so the tool's
# category argument maps straight through. `accommodation` is valid but has no
# seeded rows — the seed spec listed rest shelters and no overnight lodging.
FACILITY_CATEGORIES: dict[str, list[str]] = {
    category: [category]
    for category in ("medical", "water", "toilet", "rest", "food", "accommodation")
}


# --- language detection -----------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_MARATHI_MARKERS = ("आहे", "आहेत", "मला", "कुठे", "कसे", "काय", "किती", "नाही", "पाहिजे", "हवं", "हवे")
_HINDI_MARKERS = ("है", "हैं", "मुझे", "कहाँ", "कहां", "कैसे", "क्या", "कितना", "नहीं", "चाहिए")

LANGUAGE_NAMES = {"mr": "Marathi", "hi": "Hindi", "en": "English", "kn": "Kannada", "te": "Telugu"}


def detect_language(text: str, default: str | None = None) -> str:
    """Pick a language from the script and a few high-frequency function words."""
    if not _DEVANAGARI.search(text):
        return "en"

    marathi = sum(marker in text for marker in _MARATHI_MARKERS)
    hindi = sum(marker in text for marker in _HINDI_MARKERS)
    if hindi > marathi:
        return "hi"
    if marathi > hindi:
        return "mr"
    # Devanagari with no decisive marker: Marathi is the Wari's first language.
    return default if default in ("mr", "hi") else "mr"


# --- system prompt ----------------------------------------------------------

_BASE_PROMPT = """You are WariVerse, a compassionate AI guide for pilgrims on \
the Pandharpur Wari pilgrimage.
You help with crowd conditions, routes, facilities, temple information, and \
emergencies.
Always respond in {language_name}. Keep answers concise and practical.
Never make up crowd data — always use the provided tools."""

_APP_PROMPT = """
CHANNEL: mobile app.
- The app renders a card for every tool result, so do not read the numbers back \
in full. Add what the card cannot say: what it means and what to do next.
- Two or three short sentences.
"""

_IVR_PROMPT = """
CHANNEL: voice call (IVR). Your reply is read aloud by a text-to-speech voice \
to someone walking, often an elderly pilgrim on a bad line.
- MAXIMUM two sentences. Never longer.
- No lists, no bullet points, no numbers in digits — write "about twenty \
minutes", not "20 min".
- No markdown, no emoji, no URLs, no abbreviations.
- Say the single most important thing first.
"""

_SAFETY_PROMPT = """
SAFETY RULES:
- For any medical emergency, crush risk, or a missing person, call the \
appropriate tool immediately. Do not ask clarifying questions first.
- Never give medical advice beyond "reach the nearest medical post".
- Never invent a phone number, timing, distance or crowd figure. If a tool did \
not give it to you, say you do not have it.
"""


def build_system_prompt(
    language: str, channel: str, location: GeoPoint | None, state: SessionState | None
) -> str:
    parts = [
        _BASE_PROMPT.format(language_name=LANGUAGE_NAMES.get(language, "English")),
        _IVR_PROMPT if channel == "ivr" else _APP_PROMPT,
        _SAFETY_PROMPT,
    ]

    if location is not None:
        # Tools take explicit coordinates, so the model needs the pilgrim's
        # position to pass along.
        parts.append(
            f"\nThe pilgrim's current location is latitude {location.lat}, "
            f"longitude {location.lon}. Use it for any tool that needs coordinates."
        )
    else:
        parts.append(
            "\nThe pilgrim's location is unknown. If a tool needs coordinates, "
            "ask them where they are (near which gate, ghat or landmark) first."
        )

    parts.append(f"\nKnown zone ids: {', '.join(ZONES_BY_ID)}.")

    if state is not None and state.pending_sos:
        parts.append(
            "\nAn emergency is awaiting this pilgrim's confirmation. If they "
            "confirm, acknowledge that help is being sent."
        )
    return "".join(parts)


# --- tool schemas -----------------------------------------------------------

_ZONE_ENUM = list(ZONES_BY_ID)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_crowd_density",
            "description": (
                "Current crowd level for a monitored zone. Call this for any "
                "question about how busy, crowded or long a queue is right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {
                        "type": "string",
                        "enum": _ZONE_ENUM,
                        "description": "Zone to check.",
                    }
                },
                "required": ["zone_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_congestion_forecast",
            "description": (
                "Hourly crowd projection for the next few hours. Call this when "
                "asked when to go, or whether to wait."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id": {"type": "string", "enum": _ZONE_ENUM},
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "How many hours ahead. Defaults to 6.",
                    },
                },
                "required": ["zone_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_guidance",
            "description": (
                "Walking directions along the palkhi route between two points, "
                "including crowd warnings on the way."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "dest_lat": {"type": "number"},
                    "dest_lng": {"type": "number"},
                },
                "required": ["origin_lat", "origin_lng", "dest_lat", "dest_lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nearby_facility",
            "description": (
                "Nearest water points, toilets, medical posts, food halls, rest "
                "areas or accommodation to a coordinate. Results include free "
                "community seva offerings — annachatras, langars, donated rooms "
                "— run by local volunteers, marked with is_seva."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "category": {
                        "type": "string",
                        "enum": list(FACILITY_CATEGORIES),
                    },
                },
                "required": ["lat", "lng", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_temple_info",
            "description": (
                "Shri Vitthal Rukmini temple: darshan timings, aarti schedule, "
                "rules and current queue status."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_lost_found",
            "description": (
                "File a missing person or lost item report and get a reference "
                "number. Ask for a contact phone number before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "incident_type": {"type": "string", "enum": ["PERSON", "ITEM"]},
                    "description": {
                        "type": "string",
                        "description": (
                            "Everything a searcher needs: name, age, clothing, "
                            "where and when they were last seen."
                        ),
                    },
                    "reporter_phone": {
                        "type": "string",
                        "description": "Indian mobile number of the person reporting.",
                    },
                },
                "required": ["incident_type", "description", "reporter_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_sos",
            "description": (
                "Raise an emergency for this pilgrim. Call immediately for a "
                "medical emergency, crush risk or any threat to life. This "
                "creates a PENDING emergency that the pilgrim must confirm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "emergency_type": {
                        "type": "string",
                        "enum": [
                            "medical",
                            "lost_person",
                            "crowd_crush",
                            "fire",
                            "harassment",
                            "other",
                        ],
                    },
                },
                "required": ["lat", "lng"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Hand this conversation to a human volunteer. Use when the "
                "pilgrim asks for a person, is distressed, or the question is "
                "outside what these tools can answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "One line for the volunteer picking this up.",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


# --- results ----------------------------------------------------------------


@dataclass
class ToolOutcome:
    """What a tool produced: a widget for the client, a summary for the model.

    `summary` is what the model sees — prose-friendly and unit-labelled, not
    the widget payload, so the model never has to guess whether `distance` is
    metres or kilometres.
    """

    name: str
    widget: dict[str, Any] | None
    summary: dict[str, Any]
    ok: bool = True
    # `get_nearby_facility` returns several places but the widget describes one,
    # so the extras ride along here.
    extra_widgets: list[dict[str, Any]] = field(default_factory=list)

    @property
    def widgets(self) -> list[dict[str, Any]]:
        return ([self.widget] if self.widget else []) + self.extra_widgets


def new_message_id() -> str:
    """Client-facing message id, e.g. `assistant-1735689600123`.

    Millisecond epoch rather than seconds: two turns can land in the same
    second, and the frontend uses this as a React list key.
    """
    return f"assistant-{int(time.time() * 1000)}"


@dataclass
class OrchestratorResult:
    session_id: str  # echoed back exactly as the client sent it
    message_id: str
    language: str
    response_text: str
    widgets: list[dict[str, Any]] = field(default_factory=list)
    # Not part of the API response; logged and used by the conversation router.
    internal_session_id: UUID | None = None
    stored_message_id: UUID | None = None
    tools_called: list[str] = field(default_factory=list)
    requires_sos_confirmation: bool = False
    source: str = "rules"
    model: str | None = None
    response_time_ms: int = 0

    def to_response(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "message_id": self.message_id,
            "language": self.language,
            "response_text": self.response_text,
            "widgets": self.widgets,
        }


# --- Redis history ----------------------------------------------------------


class ConversationHistory:
    """The last N turns, at the key the spec defines.

    Distinct from `SessionService`'s state key: this is only what gets replayed
    into the model, trimmed to a fixed window. The durable transcript lives in
    the `messages` table and in `sessions.context_json`.
    """

    def __init__(self, session_id: UUID) -> None:
        self.key = HISTORY_KEY.format(session_id=session_id)

    async def load(self, limit: int = MAX_HISTORY_MESSAGES) -> list[dict[str, str]]:
        client = get_redis()
        if client is None:
            return []
        try:
            raw = await client.lrange(self.key, -limit, -1)
        except (RedisError, OSError) as exc:
            log.warning("history_read_failed", error=str(exc))
            return []

        messages: list[dict[str, str]] = []
        for item in raw:
            try:
                entry = json.loads(item)
            except json.JSONDecodeError:
                continue
            if entry.get("role") in ("user", "assistant") and entry.get("content"):
                messages.append({"role": entry["role"], "content": entry["content"]})
        return messages

    async def append(self, *entries: dict[str, str]) -> None:
        client = get_redis()
        if client is None or not entries:
            return
        try:
            await client.rpush(self.key, *(json.dumps(e) for e in entries))
            await client.ltrim(self.key, -MAX_HISTORY_MESSAGES, -1)
            await client.expire(self.key, settings.session_ttl_seconds)
        except (RedisError, OSError) as exc:
            log.warning("history_write_failed", error=str(exc))


# --- orchestrator -----------------------------------------------------------


class LLMOrchestrator:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self.crowd = CrowdService(db)
        self.facilities = FacilityService(db)
        self.routes = RouteService(db, self.crowd)
        self.sessions = SessionService(db)
        self.sos = SosService(db)
        self.temple = TempleService(db)

    async def process_message(
        self,
        *,
        session_id: str | None = None,
        user_message: str,
        language: str | None = None,
        channel: str = "app",
        location: GeoPoint | None = None,
        user_id: UUID | None = None,
        is_voice: bool = False,
    ) -> OrchestratorResult:
        """One conversation turn. `session_id` is the client's opaque string."""
        started = time.perf_counter()

        state = await self.sessions.resolve(
            session_id, user_id=user_id, language=language, channel=channel
        )
        if location is not None:
            state.last_lat, state.last_lon = location.lat, location.lon
        point = location or self._last_known_point(state)

        lang = language or detect_language(user_message, default=state.language)
        state.language = lang

        history = ConversationHistory(state.session_id)
        prior = await history.load()
        if not prior and state.messages:
            # Redis was flushed; replay what the durable copy still has.
            prior = [
                {"role": m["role"], "content": m["content"]}
                for m in state.messages[-MAX_HISTORY_MESSAGES:]
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]

        outcomes: list[ToolOutcome] = []
        if settings.llm_configured:
            reply, outcomes, source = await self._run_llm(
                user_message, lang, channel, point, state, prior
            )
        else:
            reply, outcomes, source = None, [], "rules"

        if reply is None and outcomes:
            # The model ran tools but never produced a final sentence (timeout,
            # API error, or it kept calling tools). Describe what those tools
            # returned rather than discarding them — one of them may have filed
            # a report whose reference number the pilgrim still needs.
            described: list[str] = []
            seen: set[str] = set()
            for outcome in outcomes:
                if outcome.name in seen:
                    continue
                seen.add(outcome.name)
                if text := self._describe(outcome, lang):
                    described.append(text)
            reply = " ".join(described)
            source = "rules"

        if not reply:
            reply, outcomes = await self._rule_based(user_message, lang, point, state)
            source = "rules"

        # IVR is a phone call: there is no screen to render a card on.
        widgets = (
            [w for o in outcomes for w in o.widgets] if channel != "ivr" else []
        )
        requires_confirmation = any(
            o.widget and o.widget["type"] == "sos"
            and o.widget["data"]["status"] == "CONFIRMATION_REQUIRED"
            for o in outcomes
        )
        if requires_confirmation:
            await self.sessions.set_pending_sos(state, True)
        elif state.pending_sos and not any(o.name == "trigger_sos" for o in outcomes):
            # Any unrelated message clears a stale prompt so a later "yes"
            # cannot fire an emergency the pilgrim has moved on from.
            await self.sessions.set_pending_sos(state, False)

        stored_id = await self.sessions.record_turn(
            state,
            user_message,
            reply,
            intent=outcomes[0].name if outcomes else None,
            widgets=widgets or None,
            is_voice=is_voice,
        )
        await history.append(
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        )

        response_time_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "conversation_turn",
            session_id=state.client_key or str(state.session_id),
            internal_session_id=str(state.session_id),
            channel=channel,
            language=lang,
            source=source,
            tools=[o.name for o in outcomes],
            widgets=[w["type"] for w in widgets],
            is_voice=is_voice,
            response_time_ms=response_time_ms,
        )

        return OrchestratorResult(
            session_id=state.client_key or str(state.session_id),
            message_id=new_message_id(),
            language=lang,
            response_text=reply,
            widgets=widgets,
            internal_session_id=state.session_id,
            stored_message_id=stored_id,
            tools_called=[o.name for o in outcomes],
            requires_sos_confirmation=requires_confirmation,
            source=source,
            model=settings.openai_model if source == "llm" else None,
            response_time_ms=response_time_ms,
        )

    # --- LLM loop ----------------------------------------------------------

    async def _run_llm(
        self,
        user_message: str,
        language: str,
        channel: str,
        point: GeoPoint | None,
        state: SessionState,
        prior: list[dict[str, str]],
    ) -> tuple[str | None, list[ToolOutcome], str]:
        try:
            from openai import AsyncOpenAI
        except ImportError:  # pragma: no cover — openai is a hard requirement
            log.warning("openai_package_missing")
            return None, [], "rules"

        client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(language, channel, point, state)},
            *prior,
            {"role": "user", "content": user_message},
        ]

        outcomes: list[ToolOutcome] = []
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=settings.openai_model,
                        messages=messages,  # type: ignore[arg-type]
                        tools=TOOL_SCHEMAS,  # type: ignore[arg-type]
                        tool_choice="auto",
                        temperature=0.3,
                        max_tokens=150 if channel == "ivr" else 400,
                    ),
                    timeout=settings.openai_timeout_seconds,
                )
                choice = response.choices[0].message

                if not choice.tool_calls:
                    text = (choice.content or "").strip()
                    return (text or None), outcomes, "llm"

                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in choice.tool_calls
                        ],
                    }
                )

                for call in choice.tool_calls:
                    outcome = await self._execute_tool(
                        call.function.name,
                        _parse_arguments(call.function.arguments),
                        language=language,
                        state=state,
                        point=point,
                    )
                    outcomes.append(outcome)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(outcome.summary, ensure_ascii=False)[:4000],
                        }
                    )

            # Out of rounds: answer from what the tools already returned rather
            # than looping forever.
            log.warning("llm_tool_rounds_exhausted", session_id=str(state.session_id))
            return None, outcomes, "rules"

        except (TimeoutError, asyncio.TimeoutError):
            log.warning("llm_timeout", model=settings.openai_model)
            return None, outcomes, "rules"
        except Exception as exc:  # noqa: BLE001 — any LLM failure falls back to rules
            log.warning("llm_call_failed", model=settings.openai_model, error=str(exc))
            return None, outcomes, "rules"

    # --- tool dispatch -----------------------------------------------------

    async def _execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        language: str,
        state: SessionState,
        point: GeoPoint | None,
    ) -> ToolOutcome:
        handlers = {
            "get_crowd_density": self._tool_crowd_density,
            "get_congestion_forecast": self._tool_congestion_forecast,
            "get_route_guidance": self._tool_route_guidance,
            "get_nearby_facility": self._tool_nearby_facility,
            "get_temple_info": self._tool_temple_info,
            "report_lost_found": self._tool_report_lost_found,
            "trigger_sos": self._tool_trigger_sos,
            "escalate_to_human": self._tool_escalate,
        }
        handler = handlers.get(name)
        if handler is None:
            log.warning("unknown_tool_requested", tool=name)
            return ToolOutcome(name, None, {"error": f"unknown tool: {name}"}, ok=False)

        try:
            return await handler(args, language=language, state=state, point=point)
        except Exception as exc:  # noqa: BLE001 — a broken tool must not kill the turn
            log.exception("tool_failed", tool=name)
            return ToolOutcome(name, None, {"error": str(exc)}, ok=False)

    async def _tool_crowd_density(self, args, *, language, state, point) -> ToolOutcome:
        zone_id = args.get("zone_id") or DEFAULT_ZONE
        if zone_id not in ZONES_BY_ID:
            return ToolOutcome(
                "get_crowd_density",
                None,
                {"error": f"unknown zone_id {zone_id}", "known_zones": list(ZONES_BY_ID)},
                ok=False,
            )

        reading = await self.crowd.read_zone(zone_id, language)
        advice = await self.crowd.advice(reading, language)
        alternates = await self.crowd.alternates(reading, language)

        widget = {
            "type": "crowd_density",
            "data": {
                "zone_id": reading.zone_id,
                "zone_name": reading.zone_name,
                "density": reading.density,
                "status": reading.status,
                "latitude": reading.latitude,
                "longitude": reading.longitude,
                # A rendered phrase, not a timestamp — the app prints it as-is.
                "updated_at": humanize_age(reading.recorded_at, language),
            },
        }
        return ToolOutcome(
            "get_crowd_density",
            widget,
            {
                "zone": reading.zone_name,
                "density_percent": reading.density,
                "status": reading.status,
                "wait_minutes": reading.wait_minutes,
                "advice": advice,
                "quieter_alternatives": [a.name for a in alternates],
                "data_source": reading.source,
            },
        )

    async def _tool_congestion_forecast(self, args, *, language, state, point) -> ToolOutcome:
        zone_id = args.get("zone_id") or DEFAULT_ZONE
        if zone_id not in ZONES_BY_ID:
            return ToolOutcome(
                "get_congestion_forecast",
                None,
                {"error": f"unknown zone_id {zone_id}"},
                ok=False,
            )

        hours = int(args.get("hours") or 12)
        points = await self.crowd.forecast(
            zone_id, hours=max(1, min(hours, 24)), language=language
        )
        zone_name = localized_name(ZONES_BY_ID[zone_id], language)
        recommendation = self.crowd.recommendation(zone_id, points, language)

        widget = {
            "type": "congestion_forecast",
            "data": {
                "zone_id": zone_id,
                "zone_name": zone_name,
                "points": [{"time": p["time"], "value": p["value"]} for p in points],
                "recommendation": recommendation,
                "updated_at": t(
                    "forecast_updated", language, age=humanize_age(now_utc(), language)
                ),
            },
        }
        return ToolOutcome(
            "get_congestion_forecast",
            widget,
            {
                "zone": zone_name,
                "hourly": [{"time": p["time"], "density_percent": p["value"]} for p in points],
                "recommendation": recommendation,
                "note": "projection from the time-of-day model, not a measurement",
            },
        )

    async def _tool_route_guidance(self, args, *, language, state, point) -> ToolOutcome:
        origin_lat = _as_float(args.get("origin_lat"), point.lat if point else None)
        origin_lng = _as_float(args.get("origin_lng"), point.lon if point else None)
        dest_lat = _as_float(args.get("dest_lat"), None)
        dest_lng = _as_float(args.get("dest_lng"), None)

        if None in (origin_lat, origin_lng, dest_lat, dest_lng):
            return ToolOutcome(
                "get_route_guidance",
                None,
                {"reason": "no_location", "error": "need origin and destination coordinates"},
                ok=False,
            )

        try:
            route = await self.routes.guidance(
                origin_lat=origin_lat,
                origin_lng=origin_lng,
                dest_lat=dest_lat,
                dest_lng=dest_lng,
                language=language,
            )
        except DestinationNotFoundError:
            return ToolOutcome(
                "get_route_guidance", None, {"error": "no route to that point"}, ok=False
            )

        widget = {"type": "route_guidance", "data": route.model_dump()}
        return ToolOutcome(
            "get_route_guidance",
            widget,
            {
                "destination": route.destination.label,
                "distance": route.distance,
                "estimated_time": route.estimated_time,
                "route": route.route_id,
                "avoid_areas": route.avoid_areas,
            },
        )

    async def _tool_nearby_facility(self, args, *, language, state, point) -> ToolOutcome:
        lat = _as_float(args.get("lat"), point.lat if point else None)
        lng = _as_float(args.get("lng"), point.lon if point else None)
        category = str(args.get("category") or "water")

        if lat is None or lng is None:
            # Distinct from "none found": we do not know where they are. Saying
            # "no water here" to a thirsty pilgrim whose location we never had
            # is worse than admitting we cannot tell.
            return ToolOutcome(
                "get_nearby_facility",
                None,
                {"reason": "no_location", "error": "need the pilgrim's coordinates"},
                ok=False,
            )

        types = FACILITY_CATEGORIES.get(category)
        if types is None:
            return ToolOutcome(
                "get_nearby_facility",
                None,
                {
                    "reason": "unknown_category",
                    "error": f"unknown category {category}",
                    "known": list(FACILITY_CATEGORIES),
                },
                ok=False,
            )

        found = await self.facilities.nearby(
            lat, lng, facility_types=types, limit=3, language=language
        )
        if not found:
            return ToolOutcome(
                "get_nearby_facility",
                None,
                {
                    "reason": "none_found",
                    "result": "none within range",
                    "category": category,
                },
                ok=False,
            )

        # One widget per facility: `nearby_facility` describes a single place.
        widgets = [
            {"type": "nearby_facility", "data": f.model_dump()} for f in found
        ]
        return ToolOutcome(
            "get_nearby_facility",
            widgets[0],
            {
                "category": category,
                "facilities": [
                    {
                        "name": f.name,
                        "distance_m": f.distance_m,
                        "walk_minutes": f.walk_minutes,
                        "open": f.is_open,
                        "contact": f.contact,
                        # So the model can say "a free langar run by X" rather
                        # than presenting it as an official facility.
                        "free_community_seva": f.is_seva,
                        "run_by": f.provider_name,
                    }
                    for f in found
                ],
            },
            extra_widgets=widgets[1:],
        )

    async def _tool_temple_info(self, args, *, language, state, point) -> ToolOutcome:
        info = await self.temple.get(language)
        try:
            queue = await self.crowd.read_zone(DEFAULT_ZONE)
            wait_minutes, queue_status = queue.wait_minutes, queue.status
        except ZoneNotFoundError:
            wait_minutes, queue_status = None, "MODERATE"

        widget = {"type": "temple_info", "data": info.model_dump()}
        return ToolOutcome(
            "get_temple_info",
            widget,
            {
                "temple": info.title,
                "timings": info.timings,
                "rituals": info.rituals,
                "events": info.events,
                "queue_status": queue_status,
                "queue_wait_minutes": wait_minutes,
                "contact": TEMPLE_CONTACT,
            },
        )

    async def _tool_report_lost_found(self, args, *, language, state, point) -> ToolOutcome:
        from app.models.schemas import LostFoundCreate
        from app.routers.lost_found import allocate_reference_id
        from app.models.db_models import LostFoundReport

        if self.db is None:
            return ToolOutcome(
                "report_lost_found",
                {
                    "type": "lost_and_found",
                    "data": {
                        "incident_type": str(args.get("incident_type", "PERSON")),
                        "status": "FAILED",
                        "reference_id": None,
                        "next_action": t(
                            "lost_found_offline", language,
                            helpline=settings.wari_control_room,
                        ),
                    },
                },
                {"error": "report store unavailable", "helpline": settings.wari_control_room},
                ok=False,
            )

        try:
            payload = LostFoundCreate(
                incident_type=str(args.get("incident_type", "PERSON")).upper(),
                description=str(args.get("description", "")),
                reporter_phone=str(args.get("reporter_phone", "")),
            )
        except ValueError as exc:
            return ToolOutcome(
                "report_lost_found", None, {"error": f"invalid report: {exc}"}, ok=False
            )

        report = LostFoundReport(
            reference_id=await allocate_reference_id(self.db),
            incident_type=payload.incident_type,
            description=payload.description,
            reporter_phone=payload.reporter_phone,
            status="OPEN",
        )
        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)

        log.info("lost_found_created_by_tool", reference_id=report.reference_id)
        next_action = t(
            "lost_found_created", language,
            ref_id=report.reference_id, helpline=settings.wari_control_room,
        )
        widget = {
            "type": "lost_and_found",
            "data": {
                "incident_type": report.incident_type,
                "status": report.status,
                "reference_id": report.reference_id,
                "next_action": next_action,
            },
        }
        return ToolOutcome(
            "report_lost_found",
            widget,
            {
                "reference_id": report.reference_id,
                "status": report.status,
                "helpline": settings.wari_control_room,
                "instruction": "Give the pilgrim this reference number.",
            },
        )

    async def _tool_trigger_sos(self, args, *, language, state, point) -> ToolOutcome:
        """Raise an emergency as PENDING — the model may not dispatch one.

        A false positive here sends responders away from a real emergency, so a
        human confirms before anything is activated. `POST /api/conversation/
        sos/confirm` flips it to ACTIVATED.
        """
        lat = _as_float(args.get("lat"), point.lat if point else None)
        lng = _as_float(args.get("lng"), point.lon if point else None)
        emergency_type = str(args.get("emergency_type") or "other")

        if lat is None or lng is None:
            widget = {
                "type": "sos",
                "data": {
                    "status": "FAILED",
                    "message": t(
                        "sos_no_location", language, helpline=settings.emergency_helpline
                    ),
                    "control_room_status": t("control_room_unreachable", language),
                    "timestamp": format_clock(),
                },
            }
            return ToolOutcome(
                "trigger_sos",
                widget,
                {
                    "reason": "no_location",
                    "error": "cannot dispatch without coordinates",
                    "helpline": settings.emergency_helpline,
                    "instruction": "Tell the pilgrim to call the helpline immediately.",
                },
                ok=False,
            )

        state.last_lat, state.last_lon = lat, lng
        widget = {
            "type": "sos",
            "data": {
                "status": "CONFIRMATION_REQUIRED",
                "message": t(
                    "sos_confirm_prompt", language, helpline=settings.emergency_helpline
                ),
                "control_room_status": t("control_room_standing_by", language),
                "timestamp": format_clock(),
            },
        }
        return ToolOutcome(
            "trigger_sos",
            widget,
            {
                "status": "CONFIRMATION_REQUIRED",
                "emergency_type": emergency_type,
                "helpline": settings.emergency_helpline,
                "instruction": (
                    "Tell the pilgrim to confirm, and to call the helpline if it "
                    "is immediately life-threatening."
                ),
            },
        )

    async def _tool_escalate(self, args, *, language, state, point) -> ToolOutcome:
        reason = str(args.get("reason") or "pilgrim asked for a person")
        record = await self.sessions.escalate(state, reason)
        available = _volunteers_available()

        widget = {
            "type": "human_escalation",
            "data": {
                "status": record["status"],
                "message": t(
                    "escalation_waiting" if available else "escalation_offline",
                    language,
                    helpline=settings.wari_control_room,
                ),
                "contact_available": available,
            },
        }
        return ToolOutcome(
            "escalate_to_human",
            widget,
            {
                "status": record["status"],
                "volunteer_available": available,
                "helpline": settings.wari_control_room,
            },
        )

    # --- rule-based fallback -----------------------------------------------

    async def _rule_based(
        self, text: str, language: str, point: GeoPoint | None, state: SessionState
    ) -> tuple[str, list[ToolOutcome]]:
        """Keyword router used when the LLM is unavailable.

        Runs the same tools and returns the same widget shapes, so the app
        behaves identically minus the phrasing.
        """
        intent = classify_intent(text)
        args: dict[str, Any] = {}

        if intent == "sos":
            args = {"lat": point.lat if point else None, "lng": point.lon if point else None}
            tool = "trigger_sos"
        elif intent == "crowd":
            tool = "get_crowd_density"
            args = {"zone_id": zone_from_text(text) or DEFAULT_ZONE}
        elif intent == "forecast":
            tool = "get_congestion_forecast"
            args = {"zone_id": zone_from_text(text) or DEFAULT_ZONE}
        elif intent == "facility":
            tool = "get_nearby_facility"
            args = {
                "lat": point.lat if point else None,
                "lng": point.lon if point else None,
                "category": category_from_text(text),
            }
        elif intent == "route":
            tool = "get_route_guidance"
            # No destination given, so head for the temple — which is where a
            # pilgrim asking for directions is almost always going.
            destination = zone_from_text(text)
            target = ZONES_BY_ID.get(destination or "", {})
            args = {
                "origin_lat": point.lat if point else None,
                "origin_lng": point.lon if point else None,
                "dest_lat": target.get("lat", TEMPLE_LAT),
                "dest_lng": target.get("lon", TEMPLE_LON),
            }
        elif intent == "temple":
            tool = "get_temple_info"
        elif intent == "lost_found":
            # Filing needs details the keyword router cannot extract; point the
            # pilgrim at the form instead of inventing a report.
            return t("lost_found_prompt", language), []
        elif intent == "escalate":
            tool = "escalate_to_human"
            args = {"reason": "pilgrim asked for a human"}
        elif intent == "greeting":
            return t("greeting", language), []
        else:
            return t("fallback", language), []

        outcome = await self._execute_tool(
            tool, args, language=language, state=state, point=point
        )
        return self._describe(outcome, language), [outcome]

    @staticmethod
    def _describe(outcome: ToolOutcome, language: str) -> str:
        """Deterministic sentence for a tool result, used without the LLM."""
        data: dict[str, Any] = outcome.widget["data"] if outcome.widget else {}
        summary = outcome.summary

        if not outcome.ok:
            # Emergencies and lost-person reports carry their own wording, which
            # names the helpline — that must win over any generic message.
            if outcome.name in ("trigger_sos", "report_lost_found"):
                return str(
                    data.get("message") or data.get("next_action") or t("fallback", language)
                )
            # "We don't know where you are" and "there is nothing here" are
            # different answers and must not be collapsed.
            if summary.get("reason") == "no_location":
                return t("need_location", language)
            if outcome.name == "get_nearby_facility":
                return t("facilities_none", language)
            return t("need_location", language)

        if outcome.name == "get_crowd_density":
            return str(summary.get("advice") or t("fallback", language))
        if outcome.name == "get_congestion_forecast":
            return str(data.get("recommendation"))
        if outcome.name == "get_nearby_facility":
            facilities = summary.get("facilities") or []
            return t(
                "facilities_found",
                language,
                count=len(facilities),
                nearest=data.get("name"),
                distance=data.get("distance"),
            )
        if outcome.name == "get_route_guidance":
            first = summary.get("first_step") or ""
            return f"{t('route_summary', language, destination=summary.get('destination'), distance=summary.get('distance_km'), eta=summary.get('eta_minutes'))} {first}".strip()
        if outcome.name == "get_temple_info":
            return t(
                "temple_summary",
                language,
                name=data.get("title"),
                wait=summary.get("queue_wait_minutes") or "—",
            )
        if outcome.name in ("trigger_sos", "escalate_to_human", "report_lost_found"):
            return str(data.get("message") or data.get("next_action") or "")
        return t("fallback", language)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _last_known_point(state: SessionState) -> GeoPoint | None:
        if state.last_lat is None or state.last_lon is None:
            return None
        return GeoPoint(lat=state.last_lat, lon=state.last_lon)


# --- keyword routing (fallback only) ----------------------------------------

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sos": (
        "मदत करा", "वाचवा", "बचाओ", "मदद करो", "इमर्जन्सी", "आणीबाणी", "आपत्कालीन",
        "आपातकाल", "रुग्णवाहिका", "एम्बुलेंस", "अ‍ॅम्ब्युलन्स", "अपघात", "दुर्घटना",
        "बेशुद्ध", "चक्कर", "छातीत दुखत", "सीने में दर्द", "emergency", "sos",
        "help me", "ambulance", "accident", "unconscious", "chest pain", "collapsed",
        "heart attack", "fainted", "bachao",
    ),
    "lost_found": (
        "हरवले", "हरवला", "हरवली", "सापडत नाही", "खो गया", "खो गई", "गुम",
        "missing", "lost my", "cannot find", "can't find", "kho gaya", "lost child",
    ),
    "escalate": (
        "माणसाशी", "स्वयंसेवक", "व्यक्तीशी बोला", "आदमी से", "इंसान से", "स्वयंसेवी",
        "talk to a human", "talk to someone", "speak to a person", "volunteer",
        "real person", "agent",
    ),
    "forecast": (
        "कधी जाऊ", "कधी कमी", "नंतर", "अंदाज", "कब जाऊं", "कब कम", "बाद में",
        "when should", "later", "forecast", "predict", "best time", "quieter",
    ),
    "crowd": (
        "गर्दी", "भीड", "भीड़", "रांग", "कतार", "किती वेळ", "कितनी देर", "प्रतीक्षा",
        "crowd", "rush", "queue", "how long", "wait", "waiting time", "gardi", "busy",
    ),
    # Includes the words pilgrims actually use for community seva — a question
    # like "Annachatra near me" has to reach facility search, not fall through
    # to "I didn't quite catch that".
    "facility": (
        "पाणी", "पानी", "शौचालय", "स्वच्छतागृह", "टॉयलेट", "जेवण", "भोजन", "अन्नछत्र",
        "अन्नदान", "लंगर", "भंडारा", "खाणे", "खाना", "डॉक्टर", "दवाखाना", "औषध",
        "वैद्यकीय", "चिकित्सा", "निवारा", "मुक्काम", "निवास", "पोलीस", "पुलिस",
        "मोफत", "मुफ्त", "सेवा",
        "water", "toilet", "washroom", "food", "meal", "doctor", "medical",
        "hospital", "medicine", "shelter", "rest", "stay", "accommodation",
        "nearby", "paani", "annachatra", "annachhatra", "annadan", "langar",
        "bhandara", "seva", "free food", "free stay", "lodging", "room",
    ),
    "route": (
        "रस्ता", "मार्ग", "कसे जायचे", "कैसे जाएं", "किती दूर", "कितनी दूर", "दिशा",
        "route", "way to", "how far", "how do i get", "directions", "distance to",
    ),
    "temple": (
        "दर्शन", "मंदिर", "आरती", "पूजा", "विठ्ठल", "पांडुरंग", "विट्ठल",
        "temple", "darshan", "aarti", "timing", "vitthal", "vithoba", "puja",
    ),
    "greeting": (
        "राम कृष्ण हरी", "जय हरी", "नमस्कार", "नमस्ते", "जय विठ्ठल", "hello", " hi ",
        " hey ", "good morning", "namaskar", "ram krishna hari",
    ),
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "water": ("पाणी", "पानी", "water", "paani", "तहान", "प्यास", "thirsty"),
    "toilet": ("शौचालय", "स्वच्छतागृह", "टॉयलेट", "toilet", "washroom", "restroom"),
    "medical": (
        "डॉक्टर", "दवाखाना", "औषध", "वैद्यकीय", "चिकित्सा", "प्रथमोपचार",
        "doctor", "medical", "hospital", "medicine", "first aid",
    ),
    # "annachatra" and "langar" are what pilgrims actually say for free food;
    # "seva" and "free" cover the community offerings specifically.
    "food": (
        "जेवण", "भोजन", "अन्नछत्र", "अन्नदान", "खाणे", "खाना", "भूक", "लंगर", "सेवा",
        "food", "meal", "prasad", "annachatra", "annachhatra", "annadan", "langar",
        "free food", "seva", "bhandara", "भंडारा",
    ),
    "accommodation": (
        "मुक्काम", "रात्री", "तंबू", "निवास", "राहण्याची", "ठहरने",
        "accommodation", "stay", "sleep", "night", "lodging", "free stay", "room",
    ),
    "rest": ("निवारा", "विश्रांती", "आराम", "shelter", "rest", "shade", "sit"),
}

_AFFIRMATIVE = frozenset(
    {
        "होय", "हो", "हा", "हाँ", "हां", "जरूर", "नक्की", "पाठवा", "भेजो", "भेजें",
        "yes", "yeah", "yep", "haan", "hoy", "ok", "okay", "confirm", "send",
    }
)
_NEGATIVE = frozenset({"नाही", "नको", "नहीं", "मत", "no", "nope", "cancel", "रद्द", "stop"})

# `\w` alone splits Devanagari words at vowel signs — "होय" tokenises to
# "ह" + "य" — so the Devanagari block is added explicitly.
_TOKEN_RE = re.compile(r"[\wऀ-ॿ]+")


def is_affirmative(text: str) -> bool:
    """Whole-word match only.

    Substring matching is unsafe here: this decides whether an SOS fires, and
    "ho" appears inside "how long is the queue".
    """
    tokens = set(_TOKEN_RE.findall(text.lower()))
    if tokens & _NEGATIVE:
        return False
    return bool(tokens & _AFFIRMATIVE)


def classify_intent(text: str) -> str:
    lowered = f" {text.lower().strip()} "
    scores = {
        intent: sum(1 for kw in keywords if kw in lowered)
        for intent, keywords in _INTENT_KEYWORDS.items()
    }
    scores = {k: v for k, v in scores.items() if v}
    if not scores:
        return "unknown"
    # An emergency phrase always wins, even mixed into a longer sentence.
    if "sos" in scores:
        return "sos"
    # "when is it less crowded" mentions both; the forecast is the real ask.
    if "forecast" in scores and "crowd" in scores:
        return "forecast"
    return max(scores, key=lambda k: scores[k])


def zone_from_text(text: str) -> str | None:
    """Match a zone by id or any of its localized names."""
    lowered = text.lower()
    for zone_id, zone in ZONES_BY_ID.items():
        if zone_id in lowered or zone_id.replace("-", " ") in lowered:
            return zone_id
        for lang in ("en", "mr", "hi"):
            name = localized_name(zone, lang)
            if name and name.lower() in lowered:
                return zone_id
    return None


def category_from_text(text: str) -> str:
    lowered = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return "water"


def _volunteers_available() -> bool:
    """Volunteer desks are staffed 05:00-23:00 IST."""
    from app.utils import now_ist

    return 5 <= now_ist().hour < 23


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string the model wrote; it can be wrong."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("tool_arguments_not_json", raw=raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: Any, fallback: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
