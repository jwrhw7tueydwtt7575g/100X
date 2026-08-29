"""Twilio Voice IVR: signature checks, TwiML correctness, and the call flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import itertools
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from httpx import AsyncClient

from app.config import settings
from app.services.twiml import SPEECH_LANGUAGES, VOICES, VoiceResponse, voice_for
from app.services.twilio_signature import expected_signature, is_valid

AUTH_TOKEN = "test-twilio-auth-token"
BASE_URL = "http://test"

# Sessions persist by CallSid, so every test needs its own or state leaks
# between them — a location learned in one would silently satisfy the next.
_counter = itertools.count(1)


@pytest.fixture
def call_sid() -> str:
    return f"CA{next(_counter):032d}"


@pytest.fixture
def twilio(monkeypatch):
    """Sign requests the way Twilio does, so the webhooks accept them."""
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN, raising=False)
    monkeypatch.setattr(settings, "ivr_validate_signature", True, raising=False)
    monkeypatch.setattr(settings, "ivr_public_base_url", BASE_URL, raising=False)

    def sign(path: str, params: dict[str, str]) -> dict[str, str]:
        payload = f"{BASE_URL}{path}" + "".join(
            f"{k}{params[k]}" for k in sorted(params)
        )
        digest = hmac.new(
            AUTH_TOKEN.encode(), payload.encode("utf-8"), hashlib.sha1
        ).digest()
        return {"X-Twilio-Signature": base64.b64encode(digest).decode()}

    return sign


async def post(client: AsyncClient, twilio, path: str, **params) -> str:
    params.setdefault("CallSid", f"CA{next(_counter):032d}")
    response = await client.post(path, data=params, headers=twilio(path, params))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    return response.text


def parse(xml: str) -> ElementTree.Element:
    root = ElementTree.fromstring(xml)
    assert root.tag == "Response"
    return root


# --- signature validation ---------------------------------------------------


async def test_unsigned_request_is_rejected(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    # These URLs are public and digit 9 dispatches responders.
    response = await client.post("/api/ivr/voice/answer", data={"CallSid": call_sid})
    assert response.status_code == 403


async def test_forged_signature_is_rejected(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    response = await client.post(
        "/api/ivr/voice/answer",
        data={"CallSid": call_sid},
        headers={"X-Twilio-Signature": "bm90LWEtc2lnbmF0dXJl"},
    )
    assert response.status_code == 403


async def test_tampered_parameters_are_rejected(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    headers = twilio("/api/ivr/voice/language", {"CallSid": call_sid, "Digits": "3"})
    # Same signature, different digit — an attacker replaying with a new payload.
    response = await client.post(
        "/api/ivr/voice/language",
        data={"CallSid": call_sid, "Digits": "9"},
        headers=headers,
    )
    assert response.status_code == 403


async def test_missing_auth_token_fails_closed(
    client: AsyncClient, monkeypatch, call_sid: str
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", None, raising=False)
    monkeypatch.setattr(settings, "ivr_validate_signature", True, raising=False)
    response = await client.post("/api/ivr/voice/answer", data={"CallSid": call_sid})
    # An unauthenticated endpoint that dispatches responders is worse than a
    # phone line that is down.
    assert response.status_code == 503


def test_signature_algorithm_properties() -> None:
    url = "https://wariverse.app/api/ivr/voice/answer"
    params = {"CallSid": "CA123", "From": "+919876543210", "Digits": "9"}
    token = "an-auth-token"

    signature = expected_signature(url, params, token)
    assert is_valid(url, params, signature, token)

    # Parameters are hashed in sorted order, so dict ordering cannot matter.
    reordered = {"Digits": "9", "CallSid": "CA123", "From": "+919876543210"}
    assert expected_signature(url, reordered, token) == signature

    # Any change to the URL, a parameter, or the token invalidates it.
    assert not is_valid(url + "x", params, signature, token)
    assert not is_valid(url, {**params, "Digits": "1"}, signature, token)
    assert not is_valid(url, params, signature, "another-token")


def test_signature_is_the_documented_construction() -> None:
    """base64(hmac_sha1(token, url + concat(sorted k+v)))."""
    url = "https://example.test/hook"
    params = {"b": "2", "a": "1"}
    token = "secret"

    manual = base64.b64encode(
        hmac.new(token.encode(), f"{url}a1b2".encode(), hashlib.sha1).digest()
    ).decode()
    assert expected_signature(url, params, token) == manual


# --- TwiML correctness ------------------------------------------------------


def test_say_text_is_xml_escaped() -> None:
    """Model output goes into `<Say>`; an unescaped `&` breaks the document.

    Twilio would reject it and the caller — possibly mid-emergency — would
    hear silence.
    """
    response = VoiceResponse()
    response.say('Water & food <at> "Gate 3" is 200m away', "en")
    xml = response.to_xml()

    assert "&amp;" in xml
    assert "&lt;at&gt;" in xml
    # Still parses, and the text survives intact.
    said = parse(xml).find("Say")
    assert said.text == 'Water & food <at> "Gate 3" is 200m away'


def test_attributes_are_quoted_safely() -> None:
    response = VoiceResponse()
    response.gather('/api/ivr/voice/transcribe?a=1&b="2"', language="mr")
    root = parse(response.to_xml())
    assert root.find("Gather").get("action") == '/api/ivr/voice/transcribe?a=1&b="2"'


def test_devanagari_survives_the_round_trip() -> None:
    response = VoiceResponse()
    response.say("जवळ पाणी उपलब्ध आहे", "mr")
    assert parse(response.to_xml()).find("Say").text == "जवळ पाणी उपलब्ध आहे"


def test_document_has_an_xml_declaration() -> None:
    assert VoiceResponse().to_xml().startswith('<?xml version="1.0" encoding="UTF-8"?>')


@pytest.mark.parametrize(
    ("language", "voice", "spoken"),
    [
        ("en", "Polly.Aditi", "en-IN"),
        ("hi", "Polly.Aditi", "hi-IN"),
        ("mr", "Google.mr-IN-Standard-A", "mr-IN"),
    ],
)
def test_voice_per_language(language: str, voice: str, spoken: str) -> None:
    assert voice_for(language) == (voice, spoken)


def test_unknown_language_falls_back_to_english() -> None:
    assert voice_for("kn") == VOICES["en"]


def test_every_voice_is_indian_accented() -> None:
    # A pilgrim should not hear an American voice reading Marathi place names.
    for _, spoken in VOICES.values():
        assert spoken.endswith("-IN")
    assert set(SPEECH_LANGUAGES) == set(VOICES)


# --- answer -----------------------------------------------------------------


async def test_answer_greets_in_all_three_languages(
    client: AsyncClient, twilio
) -> None:
    root = parse(await post(client, twilio, "/api/ivr/voice/answer"))

    gather = root.find("Gather")
    assert gather.get("numDigits") == "1"
    assert gather.get("input") == "dtmf"

    prompts = gather.findall("Say")
    assert len(prompts) == 3
    # Marathi first: it is the Wari's first language.
    assert [p.get("language") for p in prompts] == ["mr-IN", "hi-IN", "en-IN"]


async def test_answer_falls_back_when_no_key_is_pressed(
    client: AsyncClient, twilio
) -> None:
    root = parse(await post(client, twilio, "/api/ivr/voice/answer"))
    redirect = root.find("Redirect")
    # Silence must not end the call — default to Marathi and carry on.
    assert redirect is not None
    assert "Digits=1" in redirect.text


# --- language ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("digit", "spoken"), [("1", "mr-IN"), ("2", "hi-IN"), ("3", "en-IN")]
)
async def test_language_choice_sets_the_voice(
    client: AsyncClient, twilio, digit: str, spoken: str
) -> None:
    root = parse(
        await post(client, twilio, "/api/ivr/voice/language", Digits=digit)
    )
    gather = root.find("Gather")
    assert gather.find("Say").get("language") == spoken
    assert gather.get("action").endswith("/transcribe")


async def test_language_gather_listens_for_speech_and_digits(
    client: AsyncClient, twilio
) -> None:
    root = parse(await post(client, twilio, "/api/ivr/voice/language", Digits="3"))
    gather = root.find("Gather")
    # Digits stay live so a caller can press 9 at any point.
    assert gather.get("input") == "speech dtmf"
    assert gather.get("speechTimeout") == str(settings.ivr_speech_timeout_seconds)


async def test_unknown_digit_falls_back_to_default_language(
    client: AsyncClient, twilio
) -> None:
    root = parse(await post(client, twilio, "/api/ivr/voice/language", Digits="7"))
    assert root.find("Gather").find("Say") is not None


# --- the conversation loop --------------------------------------------------


async def test_speech_is_answered_and_the_loop_continues(
    client: AsyncClient, twilio
) -> None:
    xml = await post(
        client,
        twilio,
        "/api/ivr/voice/transcribe",
        SpeechResult="How crowded is Gate 3?",
        Confidence="0.92",
    )
    root = parse(xml)

    assert root.find("Say") is not None and root.find("Say").text
    # ...and it asks for the next thing, so the call keeps going.
    assert root.find("Gather") is not None
    assert root.find("Gather").get("action").endswith("/transcribe")


async def test_reply_is_spoken_not_listed(client: AsyncClient, twilio) -> None:
    """IVR replies must be speakable — no bullets, no markdown."""
    root = parse(
        await post(
            client, twilio, "/api/ivr/voice/transcribe",
            SpeechResult="How crowded is Gate 3?",
        )
    )
    spoken = root.find("Say").text
    for markup in ("*", "•", "\n-", "#", "](", "<"):
        assert markup not in spoken


async def test_silence_ends_the_call_politely(client: AsyncClient, twilio) -> None:
    root = parse(
        await post(client, twilio, "/api/ivr/voice/transcribe", SpeechResult="")
    )
    assert root.find("Gather") is not None  # one more chance
    assert root.find("Hangup") is not None


async def test_a_digit_during_speech_wins(client: AsyncClient, twilio) -> None:
    # The gather accepts both; a digit is unambiguous and one of them is SOS.
    root = parse(
        await post(
            client, twilio, "/api/ivr/voice/transcribe",
            SpeechResult="never mind", Digits="9",
        )
    )
    assert root.find("Say") is not None


# --- DTMF -------------------------------------------------------------------


async def choose_english(client: AsyncClient, twilio, call_sid: str) -> None:
    """What a real caller does first: press 3 for English."""
    await post(client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3")


@pytest.mark.parametrize("digit", ["1", "2"])
async def test_digit_shortcuts_answer_without_location(
    client: AsyncClient, twilio, call_sid: str, digit: str
) -> None:
    await choose_english(client, twilio, call_sid)
    root = parse(
        await post(
            client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits=digit
        )
    )
    assert root.find("Say").text


async def test_digit_three_asks_where_you_are_first(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    """"Nearest" is meaningless without a location, and guessing misdirects."""
    await choose_english(client, twilio, call_sid)
    root = parse(
        await post(client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="3")
    )
    prompt = root.find("Gather").find("Say").text
    assert "gate" in prompt.lower() or "ghat" in prompt.lower()


async def test_digit_three_answers_once_the_caller_has_said_where_they_are(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    await choose_english(client, twilio, call_sid)
    # A phone caller has no GPS, but they can say where they are.
    await post(
        client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="I am near Gate 2",
    )
    root = parse(
        await post(client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="3")
    )
    spoken = root.find("Say").text.lower()
    assert "gate" in spoken or "which gate" not in spoken


async def test_digit_nine_triggers_an_emergency(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    await choose_english(client, twilio, call_sid)
    root = parse(
        await post(
            client, twilio, "/api/ivr/voice/dtmf",
            CallSid=call_sid, Digits="9", From="+919876543210",
        )
    )
    spoken = root.find("Say").text.lower()
    assert "help has been requested" in spoken
    # The caller is not cut off after an emergency.
    assert root.find("Gather") is not None


async def test_digit_nine_without_location_promises_a_callback(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    await choose_english(client, twilio, call_sid)
    root = parse(
        await post(
            client, twilio, "/api/ivr/voice/dtmf",
            CallSid=call_sid, Digits="9", From="+919876543210",
        )
    )
    spoken = root.find("Say").text.lower()
    # A phone caller has no GPS — say what will actually happen.
    assert "call you back" in spoken
    assert "location is not known" in spoken


async def test_digit_nine_is_answered_in_the_callers_language(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="1"
    )
    root = parse(
        await post(client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="9")
    )
    # Marathi caller, Marathi answer — and a Marathi voice reading it.
    assert root.find("Say").get("language") == "mr-IN"
    assert "मदत" in root.find("Say").text


async def test_digit_zero_plays_hold_music(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    root = parse(
        await post(client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="0")
    )
    assert root.find("Play") is not None
    assert root.find("Play").text == settings.ivr_hold_music_url
    assert root.find("Say").text


async def test_unknown_digit_reprompts(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    root = parse(
        await post(client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="5")
    )
    assert root.find("Gather") is not None


# --- status -----------------------------------------------------------------


async def test_status_webhook_accepts_completion(client: AsyncClient, twilio) -> None:
    xml = await post(
        client,
        twilio,
        "/api/ivr/voice/status",
        CallStatus="completed",
        CallDuration="94",
        From="+919876543210",
    )
    assert parse(xml).tag == "Response"


async def test_status_webhook_ignores_in_progress(client: AsyncClient, twilio) -> None:
    xml = await post(
        client, twilio, "/api/ivr/voice/status", CallStatus="in-progress"
    )
    assert parse(xml) is not None


# ===========================================================================
# In-app IVR (/api/ivr/session/*) — no telephony provider involved.
# ===========================================================================

from app.services import ivr_state  # noqa: E402


@pytest.fixture
def ivr_session() -> str:
    """A session id unique per test — IVR menu position persists per session."""
    return f"ivr-{uuid4().hex[:12]}"


async def start_ivr(client: AsyncClient, session: str, **body) -> dict:
    response = await client.post(
        "/api/ivr/session/start", json={"session_id": session, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def press(client: AsyncClient, session: str, key: str, **body) -> dict:
    response = await client.post(
        "/api/ivr/session/dtmf", json={"session_id": session, "key": key, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def keys(body: dict) -> list[str]:
    return [o["key"] for o in body["options"]]


# --- the state machine, on its own ------------------------------------------


def test_start_offers_all_three_languages() -> None:
    opening = ivr_state.start()
    assert opening.state == "language"
    assert [o.key for o in opening.options] == ["1", "2", "3"]
    # All three greetings play, so a Marathi speaker is not asked in English only.
    for phrase in ("वारीव्हर्स", "वारीवर्स", "WariVerse"):
        assert phrase in opening.text


@pytest.mark.parametrize(
    ("key", "language"), [("1", "mr"), ("2", "hi"), ("3", "en")]
)
def test_language_selection_moves_to_the_service_menu(key: str, language: str) -> None:
    step = ivr_state.next_state("language", key, "en")
    assert step.state == "menu"
    assert step.action == "language_selected"
    assert ivr_state.LANGUAGE_KEYS[key] == language


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("1", "crowd_summary"),
        ("2", "temple_info"),
        ("3", "nearby_seva"),
        ("9", "enter_speech"),
    ],
)
def test_each_menu_key_maps_to_its_service(key: str, action: str) -> None:
    assert ivr_state.next_state("menu", key, "en").action == action


def test_option_four_asks_before_dispatching() -> None:
    """A mis-tap must not send responders.

    Pressing 4 moves to a confirmation state; only pressing 1 there dispatches.
    """
    step = ivr_state.next_state("menu", "4", "en")
    assert step.state == "sos_confirm"
    assert step.action == "sos_confirm"
    assert set(k.key for k in step.options) == {"1", "0"}

    assert ivr_state.next_state("sos_confirm", "1", "en").action == "sos_dispatch"
    # ...and cancelling returns to the menu without dispatching.
    cancelled = ivr_state.next_state("sos_confirm", "0", "en")
    assert cancelled.state == "menu"
    assert cancelled.action != "sos_dispatch"


def test_an_unclear_key_during_confirmation_re_asks() -> None:
    # Neither confirm nor cancel: guessing either way is unacceptable here.
    step = ivr_state.next_state("sos_confirm", "7", "en")
    assert step.state == "sos_confirm"
    assert step.action == "invalid"


@pytest.mark.parametrize("state", ["language", "menu", "speech"])
def test_invalid_keys_never_strand_the_caller(state: str) -> None:
    step = ivr_state.next_state(state, "7", "en")
    assert step.action == "invalid"
    assert step.state == state or step.state == "menu"
    assert step.options, "an invalid key must still offer somewhere to go"


def test_zero_replays_the_menu() -> None:
    step = ivr_state.next_state("menu", "0", "en")
    assert step.action == "replay"
    assert "press one" in step.text.lower()


def test_prompts_are_speakable() -> None:
    """These are read aloud: no markup, no bullets, no bare digits as symbols."""
    for language in ("mr", "hi", "en"):
        for text in (ivr_state.menu_prompt(language), ivr_state.goodbye(language)):
            for markup in ("*", "•", "#", "](", "<", "\n"):
                assert markup not in text


def test_hash_ends_the_session() -> None:
    step = ivr_state.next_state("menu", "#", "en")
    assert step.state == "ended"
    assert step.ends_session is True


def test_a_key_after_the_end_restarts_rather_than_dead_ending() -> None:
    assert ivr_state.next_state("ended", "1", "en").state == "language"


# --- full navigation over HTTP ----------------------------------------------


async def test_start_returns_the_language_menu(
    client: AsyncClient, ivr_session: str
) -> None:
    body = await start_ivr(client, ivr_session)

    assert set(body) >= {
        "session_id", "state", "language", "prompt", "options", "audio_base64"
    }
    assert body["session_id"] == ivr_session
    assert body["state"] == "language"
    assert keys(body) == ["1", "2", "3"]
    assert body["prompt"]
    # No speech provider configured in tests: text still comes back so the app
    # can fall back to on-device TTS.
    assert body["audio_base64"] is None
    assert body["media_type"] == "audio/mpeg"


async def test_a_known_language_skips_the_language_menu(
    client: AsyncClient, ivr_session: str
) -> None:
    body = await start_ivr(client, ivr_session, language="hi")
    assert body["state"] == "menu"
    assert body["language"] == "hi"
    assert "दबाएं" in body["prompt"]


async def test_full_flow_start_to_language_to_service(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session)

    chosen = await press(client, ivr_session, "3")  # English
    assert chosen["state"] == "menu"
    assert chosen["language"] == "en"
    assert set(keys(chosen)) == {"1", "2", "3", "4", "9", "0"}

    crowd = await press(client, ivr_session, "1")
    assert crowd["state"] == "menu"
    assert crowd["prompt"]
    # A dynamic answer, then the menu again so the caller is never stranded.
    assert "press one" in crowd["prompt"].lower()
    assert [w["type"] for w in crowd["widgets"]] == ["crowd_density"] * len(
        crowd["widgets"]
    )
    assert crowd["widgets"]


async def test_menu_state_survives_between_requests(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session)
    await press(client, ivr_session, "3")
    # Pressing 1 only means "crowd" because the session remembers we are in the
    # service menu, not the language menu.
    assert (await press(client, ivr_session, "1"))["widgets"]


async def test_two_sessions_do_not_share_state(client: AsyncClient) -> None:
    a, b = f"ivr-{uuid4().hex[:8]}", f"ivr-{uuid4().hex[:8]}"
    await start_ivr(client, a)
    await press(client, a, "3")
    await start_ivr(client, b)

    # `b` is still at the language menu; `1` selects Marathi for it.
    assert (await press(client, b, "1"))["language"] == "mr"
    assert (await press(client, a, "1"))["language"] == "en"


async def test_temple_option_returns_timings(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en")
    body = await press(client, ivr_session, "2")

    assert [w["type"] for w in body["widgets"]] == ["temple_info"]
    assert "darshan timings" in body["prompt"].lower()


async def test_facilities_option_needs_a_location(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en")
    body = await press(client, ivr_session, "3")
    # Without GPS it says so rather than guessing a location.
    assert "where you are" in body["prompt"].lower()
    assert body["widgets"] == []


async def test_facilities_option_answers_with_a_location(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en", latitude=17.6775, longitude=75.3283)
    body = await press(client, ivr_session, "3")

    assert body["widgets"]
    assert body["widgets"][0]["type"] == "nearby_facility"
    assert "nearest is" in body["prompt"].lower()


async def test_facilities_option_offers_only_pilgrim_facing_categories(
    client: AsyncClient, ivr_session: str
) -> None:
    """A lost & found desk must not outrank the water point someone asked for."""
    from app.config import settings

    await start_ivr(client, ivr_session, language="en", latitude=17.6775, longitude=75.3283)
    body = await press(client, ivr_session, "3")

    categories = {w["data"]["category"] for w in body["widgets"]}
    assert categories <= set(settings.facility_categories)
    assert "lost_found_desk" not in categories
    assert "police" not in categories


async def test_location_from_start_is_remembered(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en", latitude=17.6775, longitude=75.3283)
    # No coordinates on this request — they must come from the session.
    body = await press(client, ivr_session, "3")
    assert body["widgets"]


async def test_emergency_requires_confirmation_over_http(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en", latitude=17.6775, longitude=75.3283)

    asked = await press(client, ivr_session, "4")
    assert asked["state"] == "sos_confirm"
    assert set(keys(asked)) == {"1", "0"}
    assert asked["widgets"] == []  # nothing dispatched yet

    confirmed = await press(client, ivr_session, "1")
    assert confirmed["state"] == "menu"
    assert [w["type"] for w in confirmed["widgets"]] == ["sos"]
    assert confirmed["widgets"][0]["data"]["status"] == "ACTIVATED"


async def test_emergency_can_be_cancelled_over_http(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en")
    await press(client, ivr_session, "4")

    cancelled = await press(client, ivr_session, "0")
    assert cancelled["state"] == "menu"
    assert cancelled["widgets"] == []
    assert "cancelled" in cancelled["prompt"].lower()


async def test_invalid_key_offers_zero_to_replay(
    client: AsyncClient, ivr_session: str
) -> None:
    await start_ivr(client, ivr_session, language="en")
    body = await press(client, ivr_session, "7")

    assert "press zero" in body["prompt"].lower()
    assert "0" in keys(body)
    assert body["state"] == "menu"


async def test_nine_enters_speech_mode(client: AsyncClient, ivr_session: str) -> None:
    await start_ivr(client, ivr_session, language="en")
    body = await press(client, ivr_session, "9")

    assert body["state"] == "speech"
    assert "say your question" in body["prompt"].lower()
    assert keys(body) == ["0"]


async def test_zero_leaves_speech_mode(client: AsyncClient, ivr_session: str) -> None:
    await start_ivr(client, ivr_session, language="en")
    await press(client, ivr_session, "9")
    assert (await press(client, ivr_session, "0"))["state"] == "menu"


async def test_key_must_be_a_single_character(
    client: AsyncClient, ivr_session: str
) -> None:
    response = await client.post(
        "/api/ivr/session/dtmf", json={"session_id": ivr_session, "key": "12"}
    )
    assert response.status_code == 422


# --- voice mode -------------------------------------------------------------


async def test_voice_rejects_an_unsupported_format(
    client: AsyncClient, ivr_session: str
) -> None:
    response = await client.post(
        "/api/ivr/session/voice",
        files={"file": ("clip.txt", io.BytesIO(b"nope"), "text/plain")},
        data={"session_id": ivr_session},
    )
    assert response.status_code == 415


async def test_voice_rejects_an_empty_recording(
    client: AsyncClient, ivr_session: str
) -> None:
    response = await client.post(
        "/api/ivr/session/voice",
        files={"file": ("clip.webm", io.BytesIO(b""), "audio/webm")},
        data={"session_id": ivr_session},
    )
    assert response.status_code == 400


async def test_voice_503s_when_no_transcriber_is_available(
    client: AsyncClient, ivr_session: str
) -> None:
    # No API keys in the test environment.
    response = await client.post(
        "/api/ivr/session/voice",
        files={"file": ("clip.webm", io.BytesIO(b"\x1aE\xdf\xa3audio"), "audio/webm")},
        data={"session_id": ivr_session},
    )
    assert response.status_code == 503
    assert "menu keys" in response.json()["error"]["message"]


async def test_voice_transcribes_and_answers(
    client: AsyncClient, ivr_session: str, monkeypatch
) -> None:
    """Recording in, grounded answer out — the whole speech path."""
    from app.config import settings
    from app.services import stt

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    async def fake_whisper(audio, content_type, hint):
        return stt.Transcript(
            transcript="How crowded is Gate 3?",
            language="en",
            confidence=0.94,
            provider="whisper",
            duration_seconds=2.4,
        )

    monkeypatch.setattr(stt, "_whisper", fake_whisper)

    await start_ivr(client, ivr_session, language="en")
    response = await client.post(
        "/api/ivr/session/voice",
        files={"file": ("clip.webm", io.BytesIO(b"\x1aE\xdf\xa3audio"), "audio/webm")},
        data={"session_id": ivr_session},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["state"] == "speech"
    assert body["prompt"], "the pilgrim must hear an answer"
    # The answer is grounded in real crowd data, not invented.
    assert [w["type"] for w in body["widgets"]] == ["crowd_density"]
    assert body["widgets"][0]["data"]["zone_id"] == "gate-3"


async def test_spoken_language_switches_the_session(
    client: AsyncClient, ivr_session: str, monkeypatch
) -> None:
    from app.config import settings
    from app.services import stt

    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    async def fake_whisper(audio, content_type, hint):
        return stt.Transcript(
            transcript="गर्दी किती आहे?",
            language="mr",
            confidence=0.9,
            provider="whisper",
        )

    monkeypatch.setattr(stt, "_whisper", fake_whisper)

    await start_ivr(client, ivr_session, language="en")
    body = (
        await client.post(
            "/api/ivr/session/voice",
            files={"file": ("c.webm", io.BytesIO(b"\x1aE\xdf\xa3a"), "audio/webm")},
            data={"session_id": ivr_session},
        )
    ).json()
    # Answer in the language they actually spoke.
    assert body["language"] == "mr"


# --- audio synthesis --------------------------------------------------------


async def test_audio_is_returned_when_a_provider_is_configured(
    client: AsyncClient, ivr_session: str, monkeypatch
) -> None:
    import base64

    from app.services import tts

    async def fake_openai(text, language):
        yield b"ID3-fake-mp3"

    monkeypatch.setattr(tts, "openai_configured", lambda: True)
    monkeypatch.setattr(tts, "synthesize_openai", fake_openai)

    body = await start_ivr(client, ivr_session)
    assert base64.b64decode(body["audio_base64"]) == b"ID3-fake-mp3"


async def test_a_synthesis_failure_still_returns_the_prompt(
    client: AsyncClient, ivr_session: str, monkeypatch
) -> None:
    """Losing the audio must not lose the menu."""
    from app.services import tts

    async def exploding(text, language):
        raise tts.SynthesisError("provider down")
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "openai_configured", lambda: True)
    monkeypatch.setattr(tts, "synthesize_openai", exploding)

    body = await start_ivr(client, ivr_session)
    assert body["audio_base64"] is None
    assert body["prompt"]
    assert body["options"]


def test_openai_tts_audio_has_its_own_cache_namespace() -> None:
    from app.services import tts

    # ElevenLabs and OpenAI produce different audio for the same sentence; one
    # must not be served in place of the other.
    assert tts.cache_key("Gate 3 is busy.", "en") != tts.cache_key(
        "Gate 3 is busy.", "en", tts.OPENAI_PROVIDER
    )
    # ...and the default key shape is unchanged, so existing entries stay valid.
    assert tts.cache_key("x", "en") == tts.cache_key("x", "en", "")


def test_long_answers_are_trimmed_at_a_sentence_boundary() -> None:
    from app.services.voice_service import _trim

    text = "First sentence. Second sentence. Third sentence."
    trimmed = _trim(text, 30)
    assert trimmed.endswith(".")
    assert len(trimmed) <= 30
    assert " Third" not in trimmed


# --- the Twilio channel is untouched ----------------------------------------


async def test_the_telephony_webhooks_still_work(
    client: AsyncClient, twilio, call_sid: str
) -> None:
    # The in-app IVR is additive; the phone line must be unaffected.
    root = parse(await post(client, twilio, "/api/ivr/voice/answer", CallSid=call_sid))
    assert root.find("Gather") is not None


async def test_the_two_channels_use_different_paths(client: AsyncClient) -> None:
    """The in-app endpoint is open; the telephony one still refuses unsigned
    requests. They must not have been merged into one permissive path."""
    assert (
        await client.post("/api/ivr/session/start", json={"session_id": "x"})
    ).status_code == 200
    # 403 with a token configured, 503 without — either way, refused.
    assert (
        await client.post("/api/ivr/voice/answer", data={"CallSid": "CA1"})
    ).status_code in (403, 503)
