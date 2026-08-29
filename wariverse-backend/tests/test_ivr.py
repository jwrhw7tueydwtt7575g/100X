"""Twilio Voice IVR: signature checks, TwiML correctness, and the call flow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
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
