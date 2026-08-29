"""Intent routing and reply generation for the conversational assistant.

Design rules, in priority order:

1. **Safety is deterministic.** SOS wording, crowd warnings and emergency
   numbers come from `app/data/i18n.py`, never from the model.
2. **The model only phrases grounded facts.** Zone counts, facility distances
   and darshan timings are computed by the domain services and passed to the
   model as context; the prompt forbids inventing any other number.
3. **The assistant degrades, it does not fail.** With no API key, a network
   error or a timeout, the rule-based responder answers from the same context
   and the response is labelled `source="rules"`.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.config import settings
from app.data.i18n import t
from app.data.reference import ZONES_BY_ID, localized_name
from app.data.temple import temple_content
from app.models.schemas import ConversationAction, GeoPoint
from app.services.crowd_service import CrowdService, ZoneNotFoundError
from app.services.facility_service import FacilityService
from app.services.route_service import DestinationNotFoundError, RouteService
from app.services.session_service import SessionState

log = structlog.get_logger(__name__)

# --- language detection -----------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_MARATHI_MARKERS = ("आहे", "आहेत", "मला", "कुठे", "कसे", "काय", "किती", "नाही", "पाहिजे", "हवं", "हवे")
_HINDI_MARKERS = ("है", "हैं", "मुझे", "कहाँ", "कहां", "कैसे", "क्या", "कितना", "नहीं", "चाहिए")


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


# --- intent classification --------------------------------------------------

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sos": (
        "मदत करा", "वाचवा", "बचाओ", "मदद करो", "इमर्जन्सी", "आणीबाणी", "आपत्कालीन",
        "आपातकाल", "रुग्णवाहिका", "एम्बुलेंस", "अ‍ॅम्ब्युलन्स", "अपघात", "दुर्घटना",
        "बेशुद्ध", "चक्कर", "छातीत दुखत", "सीने में दर्द", "श्वास", "emergency", "sos",
        "help me", "ambulance", "accident", "unconscious", "chest pain", "collapsed",
        "heart attack", "fainted", "bachao",
    ),
    "lost_found": (
        "हरवले", "हरवला", "हरवली", "हरवलं", "सापडत नाही", "खो गया", "खो गई", "गुम",
        "मिल नहीं", "missing", "lost my", "cannot find", "can't find", "kho gaya",
        "harvale", "lost child", "lost person",
    ),
    "crowd": (
        "गर्दी", "भीड", "भीड़", "रांग", "कतार", "किती वेळ", "कितनी देर", "प्रतीक्षा",
        "crowd", "rush", "queue", "how long", "wait", "waiting time", "gardi",
    ),
    "facility": (
        "पाणी", "पानी", "शौचालय", "स्वच्छतागृह", "टॉयलेट", "जेवण", "भोजन", "अन्नछत्र",
        "खाणे", "खाना", "डॉक्टर", "दवाखाना", "औषध", "वैद्यकीय", "चिकित्सा", "निवारा",
        "मुक्काम", "चार्जिंग", "पोलीस", "पुलिस", "water", "toilet", "washroom",
        "food", "meal", "doctor", "medical", "hospital", "medicine", "shelter",
        "charging", "police", "facility", "nearby", "paani",
    ),
    "route": (
        "रस्ता", "मार्ग", "कसे जायचे", "कसं जायचं", "कैसे जाएं", "कैसे जाऊं", "किती दूर",
        "कितनी दूर", "अंतर", "दिशा", "route", "way to", "how far", "how do i get",
        "directions", "distance to", "reach",
    ),
    "temple": (
        "दर्शन", "मंदिर", "आरती", "पूजा", "गाभारा", "गर्भगृह", "विठ्ठल", "पांडुरंग",
        "विट्ठल", "temple", "darshan", "aarti", "timing", "vitthal", "vithoba",
        "pandurang", "puja",
    ),
    # The Latin greetings are padded because the text is space-padded before
    # matching: bare "hi" would otherwise fire on "chai", "this", …
    "greeting": (
        "राम कृष्ण हरी", "जय हरी", "नमस्कार", "नमस्ते", "जय विठ्ठल", "hello", " hi ",
        " hey ", "good morning", "namaskar", "ram krishna hari",
    ),
}

# Which facility types a phrase is asking for.
_FACILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "water": ("पाणी", "पानी", "water", "paani", "thirsty", "तहान", "प्यास"),
    "toilet": ("शौचालय", "स्वच्छतागृह", "टॉयलेट", "toilet", "washroom", "restroom", "बाथरूम"),
    "medical": (
        "डॉक्टर", "दवाखाना", "औषध", "वैद्यकीय", "चिकित्सा", "doctor", "medical",
        "hospital", "medicine", "first aid", "प्रथमोपचार",
    ),
    "food": ("जेवण", "भोजन", "अन्नछत्र", "खाणे", "खाना", "food", "meal", "prasad", "भूक"),
    "shelter": ("निवारा", "मुक्काम", "तंबू", "shelter", "stay", "sleep", "रात्री"),
    "charging": ("चार्जिंग", "चार्ज", "charging", "charge", "battery", "power"),
    "police": ("पोलीस", "पुलिस", "police", "chowky", "चौकी"),
    "lost_found_desk": ("हरवले", "खोया", "lost and found", "lost & found"),
    "bathing_ghat": ("स्नान", "आंघोळ", "नहान", "bath", "bathing", "ghat", "घाट"),
}

_AFFIRMATIVE = frozenset(
    {
        "होय", "हो", "हा", "हाँ", "हां", "जरूर", "नक्की", "पाठवा", "भेजो", "भेजें",
        "yes", "yeah", "yep", "haan", "hoy", "ok", "okay", "confirm", "send",
    }
)
_NEGATIVE = frozenset(
    {"नाही", "नको", "नहीं", "मत", "no", "nope", "cancel", "रद्द", "stop"}
)


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


def classify_intent(text: str) -> tuple[str, float]:
    """Keyword classifier. Returns (intent, confidence in 0..1)."""
    lowered = f" {text.lower().strip()} "
    scores: dict[str, int] = {}

    for intent, keywords in _INTENT_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lowered)
        if hits:
            scores[intent] = hits

    if not scores:
        return "unknown", 0.2

    # An emergency phrase always wins, even mixed into a longer sentence.
    if "sos" in scores:
        return "sos", min(0.6 + 0.15 * scores["sos"], 0.99)

    intent = max(scores, key=lambda k: scores[k])
    confidence = min(0.55 + 0.15 * scores[intent], 0.95)
    if len(scores) > 1:
        confidence -= 0.1
    return intent, round(max(confidence, 0.3), 2)


def facility_types_for(text: str) -> list[str]:
    lowered = text.lower()
    types = [ft for ft, kws in _FACILITY_KEYWORDS.items() if any(kw in lowered for kw in kws)]
    return types or ["water", "toilet", "medical", "food"]


# --- orchestration ----------------------------------------------------------


@dataclass
class OrchestratorResult:
    reply: str
    language: str
    intent: str
    confidence: float
    actions: list[ConversationAction] = field(default_factory=list)
    data: dict[str, Any] | None = None
    requires_sos_confirmation: bool = False
    source: str = "rules"
    model: str | None = None
    latency_ms: int = 0


_SYSTEM_PROMPT = """You are WariVerse, an assistant for pilgrims walking the \
Pandharpur Wari in Maharashtra, India. Millions of people walk this route, many \
of them elderly or travelling with children.

Rules you must follow:
- Reply ONLY in this language code: {language}. Use simple, warm, respectful words.
- Keep replies to at most three short sentences. Pilgrims read this while walking.
- Use ONLY the facts given in CONTEXT. Never invent timings, distances, counts, \
phone numbers or place names. If CONTEXT is empty, say what you can help with instead.
- Never give medical advice beyond "reach the nearest medical post".
- Do not add greetings unless the pilgrim greeted you first.

CONTEXT (JSON, may be empty):
{context}
"""


class LLMOrchestrator:
    """Turns a pilgrim's message into a grounded, localized reply."""

    def __init__(self, db: Any = None) -> None:
        self.crowd = CrowdService(db)
        self.facilities = FacilityService(db)
        self.routes = RouteService(db, self.crowd)

    async def respond(
        self,
        text: str,
        state: SessionState,
        language: str | None = None,
        location: GeoPoint | None = None,
    ) -> OrchestratorResult:
        started = time.perf_counter()
        lang = language or detect_language(text, default=state.language)
        intent, confidence = classify_intent(text)

        context, actions, requires_sos = await self._gather(text, lang, intent, location, state)

        if intent == "sos":
            # Safety wording is fixed: no model in the loop.
            reply = t("sos_confirm_prompt", lang, helpline=settings.emergency_helpline)
            source, model = "rules", None
        else:
            reply, source, model = await self._compose(text, lang, intent, context, state)

        return OrchestratorResult(
            reply=reply,
            language=lang,
            intent=intent,
            confidence=confidence,
            actions=actions,
            data=context or None,
            requires_sos_confirmation=requires_sos,
            source=source,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- data gathering ----------------------------------------------------

    async def _gather(
        self,
        text: str,
        language: str,
        intent: str,
        location: GeoPoint | None,
        state: SessionState,
    ) -> tuple[dict[str, Any], list[ConversationAction], bool]:
        point = location or self._last_known_point(state)
        context: dict[str, Any] = {}
        actions: list[ConversationAction] = []

        if intent == "sos":
            actions.append(
                ConversationAction(
                    type="confirm_sos",
                    label={"mr": "होय, मदत पाठवा", "hi": "हाँ, मदद भेजें"}.get(
                        language, "Yes, send help"
                    ),
                    payload={"session_id": str(state.session_id)},
                )
            )
            actions.append(
                ConversationAction(
                    type="call_number",
                    label=settings.emergency_helpline,
                    payload={"number": settings.emergency_helpline},
                )
            )
            return context, actions, True

        if intent == "crowd":
            zone_id = self._zone_from_text(text) or (
                await self.crowd.nearest_zone_id(point.lat, point.lon) if point else None
            )
            zone_id = zone_id or "vitthal_temple"
            try:
                crowd = await self.crowd.get_zone(zone_id, language)
            except ZoneNotFoundError:
                crowd = None
            if crowd is not None:
                context["crowd"] = crowd.model_dump(mode="json")
                actions.append(
                    ConversationAction(
                        type="show_crowd",
                        label={"mr": "गर्दीचा नकाशा", "hi": "भीड़ मानचित्र"}.get(
                            language, "View crowd map"
                        ),
                        payload={"zone_id": zone_id},
                    )
                )

        elif intent == "facility":
            if point is None:
                context["missing_location"] = True
            else:
                types = facility_types_for(text)
                found = await self.facilities.nearby(
                    point.lat, point.lon, facility_types=types, limit=5, language=language
                )
                context["facilities"] = [f.model_dump(mode="json") for f in found]
                context["facility_types"] = types
                if found:
                    actions.append(
                        ConversationAction(
                            type="show_facilities",
                            label={"mr": "जवळच्या सुविधा", "hi": "नज़दीकी सुविधाएँ"}.get(
                                language, "Nearby facilities"
                            ),
                            payload={"types": types, "lat": point.lat, "lon": point.lon},
                        )
                    )

        elif intent == "route":
            if point is None:
                context["missing_location"] = True
            else:
                destination = self._zone_from_text(text) or "vitthal_temple"
                try:
                    guidance = await self.routes.guidance(
                        point.lat, point.lon, destination=destination, language=language
                    )
                except DestinationNotFoundError:
                    guidance = None
                if guidance is not None:
                    context["route"] = guidance.model_dump(mode="json")
                    actions.append(
                        ConversationAction(
                            type="show_route",
                            label={"mr": "मार्ग पहा", "hi": "रास्ता देखें"}.get(
                                language, "View route"
                            ),
                            payload={"destination": destination},
                        )
                    )

        elif intent == "temple":
            content = temple_content(language)
            try:
                queue = await self.crowd.read_zone("darshan_queue")
                wait_minutes = queue.wait_minutes
                queue_level = queue.density_level
            except ZoneNotFoundError:
                wait_minutes, queue_level = None, "moderate"
            context["temple"] = {
                "name": content["name"],
                "darshan_types": content["darshan_types"],
                "aarti_schedule": content["aarti_schedule"],
                "queue_status": queue_level,
                "live_wait_minutes": wait_minutes,
            }
            actions.append(
                ConversationAction(
                    type="show_temple",
                    label={"mr": "मंदिर माहिती", "hi": "मंदिर जानकारी"}.get(
                        language, "Temple information"
                    ),
                    payload={},
                )
            )

        elif intent == "lost_found":
            context["lost_found"] = {
                "helpline": settings.wari_control_room,
                "instruction": t("lost_found_prompt", language),
            }
            actions.append(
                ConversationAction(
                    type="open_lost_found",
                    label={"mr": "हरवल्याची नोंद करा", "hi": "गुमशुदगी दर्ज करें"}.get(
                        language, "Report lost person"
                    ),
                    payload={},
                )
            )
            if point is not None:
                desk = await self.facilities.nearest(
                    point.lat, point.lon, ["lost_found_desk", "police"], language=language
                )
                if desk is not None:
                    context["lost_found"]["nearest_desk"] = desk.model_dump(mode="json")

        return context, actions, False

    # --- reply composition -------------------------------------------------

    async def _compose(
        self,
        text: str,
        language: str,
        intent: str,
        context: dict[str, Any],
        state: SessionState,
    ) -> tuple[str, str, str | None]:
        if settings.llm_configured:
            reply = await self._call_llm(text, language, context, state)
            if reply:
                return reply, "llm", settings.openai_model
        return self._rule_based(language, intent, context), "rules", None

    async def _call_llm(
        self, text: str, language: str, context: dict[str, Any], state: SessionState
    ) -> str | None:
        try:
            import json

            from openai import AsyncOpenAI
        except ImportError:  # pragma: no cover — openai is a hard requirement in prod
            log.warning("openai_package_missing")
            return None

        try:
            client = AsyncOpenAI(
                api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds
            )
            messages: list[dict[str, str]] = [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT.format(
                        language=language,
                        context=json.dumps(context, ensure_ascii=False)[:4000],
                    ),
                },
                *state.history_for_llm(settings.llm_max_history_turns),
                {"role": "user", "content": text},
            ]
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=settings.openai_model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.3,
                    max_tokens=300,
                ),
                timeout=settings.openai_timeout_seconds,
            )
            reply = (response.choices[0].message.content or "").strip()
            return reply or None
        except (TimeoutError, asyncio.TimeoutError):
            log.warning("llm_timeout", model=settings.openai_model)
            return None
        except Exception as exc:  # noqa: BLE001 — any LLM failure falls back to rules
            log.warning("llm_call_failed", model=settings.openai_model, error=str(exc))
            return None

    @staticmethod
    def _rule_based(language: str, intent: str, context: dict[str, Any]) -> str:
        if context.get("missing_location"):
            return t("need_location", language)

        if intent == "greeting":
            return t("greeting", language)

        if intent == "crowd" and context.get("crowd"):
            return context["crowd"]["advice"]

        if intent == "facility":
            facilities = context.get("facilities") or []
            if not facilities:
                return t("facilities_none", language)
            nearest = facilities[0]
            return t(
                "facilities_found",
                language,
                count=len(facilities),
                nearest=nearest["name"],
                distance=nearest["distance_m"],
            )

        if intent == "route" and context.get("route"):
            route = context["route"]
            summary = t(
                "route_summary",
                language,
                destination=route["destination_name"],
                distance=route["distance_km"],
                eta=route["eta_minutes"],
            )
            first_step = route["steps"][0]["instruction"] if route["steps"] else ""
            return f"{summary} {first_step}".strip()

        if intent == "temple" and context.get("temple"):
            temple = context["temple"]
            return t(
                "temple_summary",
                language,
                name=temple["name"],
                wait=temple.get("live_wait_minutes") or "—",
            )

        if intent == "lost_found":
            return t("lost_found_prompt", language)

        return t("fallback", language)

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _last_known_point(state: SessionState) -> GeoPoint | None:
        if state.last_lat is None or state.last_lon is None:
            return None
        return GeoPoint(lat=state.last_lat, lon=state.last_lon)

    @staticmethod
    def _zone_from_text(text: str) -> str | None:
        """Match a zone by its id or any of its localized names."""
        lowered = text.lower()
        for zone_id, zone in ZONES_BY_ID.items():
            if zone_id.replace("_", " ") in lowered or zone_id in lowered:
                return zone_id
            for lang in ("en", "mr", "hi"):
                name = localized_name(zone, lang)
                if name and name.lower() in lowered:
                    return zone_id
        return None
