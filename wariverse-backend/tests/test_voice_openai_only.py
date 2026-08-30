"""The production voice pipeline: OpenAI for everything.

`VOICE_OPENAI_ONLY` is on by default, so Whisper transcribes and `tts-1` speaks
regardless of which other provider keys happen to be present. The multi-provider
chain is still implemented and still tested — see test_voice.py, which turns the
flag off — but nothing reaches for it in a default deployment.
"""

from __future__ import annotations

import io

import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import stt, tts

pytestmark = pytest.mark.anyio

AUDIO = b"\x1aE\xdf\xa3fake-webm-audio-payload" * 8


def upload(content_type: str = "audio/webm"):
    return {"file": ("speech.webm", io.BytesIO(AUDIO), content_type)}


@pytest.fixture
def every_key(monkeypatch):
    """Every provider configured, so routing is a choice and not a fallback."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-key", raising=False)
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key", raising=False)
    monkeypatch.setattr(settings, "google_tts_api_key", "g-key", raising=False)
    monkeypatch.setattr(settings, "voice_openai_only", True, raising=False)


@pytest.fixture
def hosts(monkeypatch):
    """Record every outbound host, and script the replies."""
    seen: list[str] = []

    def install(handler):
        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.host)
            return handler(request)

        transport = httpx.MockTransport(record)
        original = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        return seen

    return install


def whisper_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "text": "Where is the nearest water point?",
            "language": "marathi",
            "duration": 2.4,
            "segments": [{"avg_logprob": -0.1}],
        },
    )


def tts_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"ID3-fake-mp3-bytes")


# --- speech in --------------------------------------------------------------


async def test_transcription_goes_to_whisper_even_with_deepgram_configured(
    client: AsyncClient, every_key, hosts
) -> None:
    seen = hosts(whisper_ok)

    response = await client.post("/api/voice/transcribe", files=upload())

    assert response.status_code == 200
    assert response.json()["provider"] == "whisper"
    assert any("openai" in host for host in seen)
    assert not any("deepgram" in host for host in seen), "Deepgram must not be called"


async def test_no_deepgram_rescue_when_whisper_fails(
    client: AsyncClient, every_key, hosts
) -> None:
    """Whisper being down is a 503, not a quiet hand-off to another vendor."""
    seen = hosts(lambda request: httpx.Response(500, json={"error": "down"}))

    response = await client.post("/api/voice/transcribe", files=upload())

    assert response.status_code == 503
    assert not any("deepgram" in host for host in seen)


# --- speech out -------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "hi", "mr"])
def test_every_language_routes_to_openai(language: str, every_key) -> None:
    assert tts.active_provider(language) == tts.OPENAI_PROVIDER
    assert tts.active_is_configured(language) is True


async def test_speak_uses_openai_not_elevenlabs(
    client: AsyncClient, every_key, hosts
) -> None:
    seen = hosts(tts_ok)

    response = await client.post(
        "/api/voice/speak", json={"text": "Gate three is busy.", "language": "en"}
    )

    assert response.status_code == 200
    assert response.headers["x-tts-provider"] == "openai"
    assert any("openai" in host for host in seen)
    assert not any("elevenlabs" in host for host in seen)


async def test_marathi_no_longer_needs_a_google_key(
    client: AsyncClient, monkeypatch, hosts
) -> None:
    """Marathi used to require GOOGLE_TTS_API_KEY. One OpenAI key now covers it."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-key", raising=False)
    monkeypatch.setattr(settings, "google_tts_api_key", None, raising=False)
    monkeypatch.setattr(settings, "google_application_credentials", None, raising=False)
    monkeypatch.setattr(settings, "voice_openai_only", True, raising=False)
    seen = hosts(tts_ok)

    response = await client.post(
        "/api/voice/speak", json={"text": "गर्दी आहे.", "language": "mr"}
    )

    assert response.status_code == 200
    assert not any("google" in host for host in seen)


async def test_speak_without_an_openai_key_is_503(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key", raising=False)
    monkeypatch.setattr(settings, "voice_openai_only", True, raising=False)

    response = await client.post(
        "/api/voice/speak", json={"text": "Hello.", "language": "en"}
    )

    assert response.status_code == 503
    assert "openai" in response.json()["error"]["message"]


# --- the GET form -----------------------------------------------------------


async def test_get_speak_returns_playable_audio(
    client: AsyncClient, every_key, hosts
) -> None:
    """`<audio src>` and the RN player can only use a URL — neither can POST."""
    hosts(tts_ok)

    response = await client.get(
        "/api/voice/speak", params={"text": "Gate three is busy.", "language": "en"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"ID3-fake-mp3-bytes"


async def test_get_and_post_speak_agree(client: AsyncClient, every_key, hosts) -> None:
    hosts(tts_ok)

    got = await client.get("/api/voice/speak", params={"text": "Same words.", "language": "hi"})
    posted = await client.post(
        "/api/voice/speak", json={"text": "Same words.", "language": "hi"}
    )

    assert got.content == posted.content
    assert got.headers["x-tts-provider"] == posted.headers["x-tts-provider"]


async def test_get_speak_rejects_empty_text(client: AsyncClient, every_key) -> None:
    assert (await client.get("/api/voice/speak", params={"text": ""})).status_code == 422


async def test_get_speak_enforces_the_length_cap(client: AsyncClient, every_key) -> None:
    long_text = "a" * (settings.tts_max_characters + 1)
    response = await client.get("/api/voice/speak", params={"text": long_text})
    assert response.status_code == 413


async def test_a_provider_failure_is_503_not_silent_audio(
    client: AsyncClient, every_key, hosts
) -> None:
    """A rate-limited or broken provider must not look like success.

    Streaming straight through meant OpenAI returning 429 produced `200 OK` with
    an empty body: the app played nothing, believed the prompt had been spoken,
    and never fell back to the device voice. Observed live against a quota-
    limited account.
    """
    hosts(lambda request: httpx.Response(429, json={"error": "rate limited"}))

    response = await client.get(
        "/api/voice/speak", params={"text": "Gate three is busy.", "language": "en"}
    )

    assert response.status_code == 503
    assert response.content != b""


async def test_an_empty_clip_is_503_not_a_zero_byte_success(
    client: AsyncClient, every_key, hosts
) -> None:
    hosts(lambda request: httpx.Response(200, content=b""))

    response = await client.post(
        "/api/voice/speak", json={"text": "Anything.", "language": "en"}
    )

    assert response.status_code == 503


# --- transport safety -------------------------------------------------------


def test_api_keys_are_never_sent_over_an_unverified_connection() -> None:
    """These requests carry `Authorization: Bearer <OPENAI_API_KEY>`.

    `verify=False` was disabling certificate checks on exactly those calls,
    which hands the key to anyone able to intercept the connection.
    """
    from pathlib import Path

    for module in ("stt.py", "tts.py", "facility_service.py"):
        source = Path("app/services") / module
        assert "verify=False" not in source.read_text(encoding="utf-8"), module
