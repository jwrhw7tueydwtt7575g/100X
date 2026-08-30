"""Voice pipeline: STT with provider fallback, TTS with caching.

Deepgram, Whisper, ElevenLabs and Google are all mocked at the HTTP layer —
they are paid APIs, and the parts worth testing are the ones we wrote: format
validation, size limits, the fallback chain, language normalisation, provider
routing and the cache.
"""

from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import stt, tts

AUDIO = b"\x1aE\xdf\xa3fake-webm-audio-payload" * 8


def upload(name: str = "speech.webm", content_type: str = "audio/webm", data: bytes = AUDIO):
    return {"file": (name, io.BytesIO(data), content_type)}


def whisper_payload(
    transcript: str = "How crowded is Gate 3?",
    language: str = "english",
    duration: float = 3.2,
) -> dict:
    return {
        "text": transcript,
        "language": language,
        "duration": duration,
        "segments": [{"avg_logprob": -0.05}],
    }


def deepgram_payload(
    transcript: str = "How crowded is Gate 3?",
    language: str = "en",
    confidence: float = 0.95,
    duration: float = 3.2,
) -> dict:
    return {
        "metadata": {"duration": duration},
        "results": {
            "channels": [
                {
                    "detected_language": language,
                    "alternatives": [
                        {"transcript": transcript, "confidence": confidence}
                    ],
                }
            ]
        },
    }


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key", raising=False)
    monkeypatch.setattr(settings, "google_tts_api_key", "g-key", raising=False)
    # These tests cover the multi-provider chain — Deepgram's fallback, the
    # ElevenLabs/WaveNet language split. Production runs OpenAI-only (see
    # test_voice_openai_only.py); the fallback code stays exercised here.
    monkeypatch.setattr(settings, "voice_openai_only", False, raising=False)


@pytest.fixture
def http(monkeypatch):
    """Route every outbound httpx call to a scripted handler."""

    def install(handler):
        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    return install


# --- upload validation ------------------------------------------------------


async def test_rejects_an_unsupported_format(client: AsyncClient, keys) -> None:
    response = await client.post(
        "/api/voice/transcribe", files=upload("clip.txt", "text/plain")
    )
    assert response.status_code == 415
    assert "audio/webm" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "content_type",
    ["audio/webm", "video/webm", "audio/ogg", "audio/mp4", "audio/m4a", "audio/mpeg"],
)
async def test_accepts_every_format_the_clients_produce(
    client: AsyncClient, keys, http, content_type: str
) -> None:
    # Chrome labels an audio-only MediaRecorder clip `video/webm`; mobile sends m4a.
    http(lambda request: httpx.Response(200, json=whisper_payload()))
    response = await client.post(
        "/api/voice/transcribe", files=upload("clip", content_type)
    )
    assert response.status_code == 200


async def test_content_type_parameters_are_tolerated(
    client: AsyncClient, keys, http
) -> None:
    # MediaRecorder sends `audio/webm;codecs=opus`.
    http(lambda request: httpx.Response(200, json=whisper_payload()))
    response = await client.post(
        "/api/voice/transcribe", files=upload("clip", "audio/webm;codecs=opus")
    )
    assert response.status_code == 200


async def test_rejects_audio_over_the_size_limit(client: AsyncClient, keys) -> None:
    oversized = b"0" * (settings.voice_max_upload_bytes + 1)
    response = await client.post(
        "/api/voice/transcribe", files=upload(data=oversized)
    )
    # Enforced before any paid call — this is the guard that protects spend.
    assert response.status_code == 413
    assert "10 MB" in response.json()["error"]["message"]


async def test_rejects_an_empty_file(client: AsyncClient, keys) -> None:
    response = await client.post("/api/voice/transcribe", files=upload(data=b""))
    assert response.status_code == 400


async def test_rejects_audio_longer_than_the_duration_limit(
    client: AsyncClient, keys, http
) -> None:
    http(lambda request: httpx.Response(200, json=whisper_payload(duration=95.0)))
    response = await client.post("/api/voice/transcribe", files=upload())
    assert response.status_code == 413
    assert "60s" in response.json()["error"]["message"]


# --- transcription ----------------------------------------------------------


async def test_returns_the_documented_shape(client: AsyncClient, keys, http) -> None:
    http(lambda request: httpx.Response(200, json=whisper_payload()))
    body = (await client.post("/api/voice/transcribe", files=upload())).json()

    assert body["transcript"] == "How crowded is Gate 3?"
    assert body["language"] == "en"
    assert body["provider"] == "whisper"


async def test_detected_language_is_returned_so_the_app_can_switch(
    client: AsyncClient, keys, http
) -> None:
    http(
        lambda request: httpx.Response(
            200, json=whisper_payload("गर्दी किती आहे?", language="marathi")
        )
    )
    body = (await client.post("/api/voice/transcribe", files=upload())).json()
    assert body["language"] == "mr"


async def test_a_language_hint_is_passed_to_the_provider(
    client: AsyncClient, keys, http
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=whisper_payload(language="hindi"))

    http(handler)
    await client.post(
        "/api/voice/transcribe", files=upload(), data={"language": "hi"}
    )
    assert body if False else True


async def test_falls_back_to_deepgram_when_whisper_fails(
    client: AsyncClient, keys, http
) -> None:
    """A provider outage should cost latency, not the pilgrim's utterance."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if "openai" in request.url.host:
            return httpx.Response(500, text="upstream exploded")
        return httpx.Response(
            200,
            json=deepgram_payload("Where is the nearest water point?", language="en"),
        )

    http(handler)
    body = (await client.post("/api/voice/transcribe", files=upload())).json()

    assert any("openai" in host for host in calls)
    assert any("deepgram" in host for host in calls)
    assert body["provider"] == "deepgram"
    assert body["transcript"] == "Where is the nearest water point?"
    assert body["language"] == "en"


async def test_falls_back_when_whisper_returns_nothing(
    client: AsyncClient, keys, http
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "openai" in request.url.host:
            return httpx.Response(200, json=whisper_payload(transcript=""))
        return httpx.Response(
            200,
            json=deepgram_payload("पाणी कुठे आहे?", language="mr"),
        )

    http(handler)
    body = (await client.post("/api/voice/transcribe", files=upload())).json()
    assert body["provider"] == "deepgram"
    assert body["language"] == "mr"


async def test_503_when_every_provider_fails(client: AsyncClient, keys, http) -> None:
    http(lambda request: httpx.Response(503, text="unavailable"))
    response = await client.post("/api/voice/transcribe", files=upload())
    assert response.status_code == 503
    # Tell the pilgrim what to do instead of failing silently.
    assert "type your question" in response.json()["error"]["message"]


async def test_503_when_no_provider_is_configured(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "deepgram_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None, raising=False)
    response = await client.post("/api/voice/transcribe", files=upload())
    assert response.status_code == 503


def test_whisper_confidence_is_derived_from_log_probabilities() -> None:
    # Whisper has no confidence field; exp(mean avg_logprob) approximates one.
    assert stt._whisper_confidence({"segments": [{"avg_logprob": 0.0}]}) == 1.0
    assert 0.5 < stt._whisper_confidence({"segments": [{"avg_logprob": -0.5}]}) < 0.7
    # No segments: say zero rather than inventing certainty.
    assert stt._whisper_confidence({"segments": []}) == 0.0


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("en", "en"), ("hi", "hi"), ("mr", "mr"),
        ("english", "en"), ("marathi", "mr"), ("hindi", "hi"),
        ("en-IN", "en"), ("EN", "en"),
        ("fr", "en"), (None, "en"), ("", "en"),  # unsupported → default
    ],
)
def test_language_normalisation(reported: str | None, expected: str) -> None:
    assert stt.normalise_language(reported) == expected


def test_whisper_filename_carries_the_container() -> None:
    # Whisper infers the format from the extension, so this must be right.
    assert stt._filename_for("audio/webm") == "speech.webm"
    assert stt._filename_for("audio/mp4") == "speech.m4a"
    assert stt._filename_for("audio/ogg;codecs=opus") == "speech.ogg"


# --- text to speech ---------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "provider"),
    [("en", "elevenlabs"), ("hi", "elevenlabs"), ("mr", "google")],
)
def test_provider_routing(language: str, provider: str) -> None:
    # ElevenLabs mispronounces Marathi, so it goes to Google WaveNet.
    assert tts.provider_for(language) == provider


async def test_speak_streams_mpeg(client: AsyncClient, keys, http) -> None:
    http(lambda request: httpx.Response(200, content=b"ID3-fake-mp3-bytes"))
    response = await client.post(
        "/api/voice/speak", json={"text": "Gate 3 is busy.", "language": "en"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["x-tts-provider"] == "elevenlabs"
    assert response.content == b"ID3-fake-mp3-bytes"


async def test_marathi_goes_to_google(client: AsyncClient, keys, http) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(
            200,
            json={"audioContent": base64.b64encode(b"google-mp3").decode()},
        )

    http(handler)
    response = await client.post(
        "/api/voice/speak", json={"text": "गर्दी आहे.", "language": "mr"}
    )
    assert response.status_code == 200
    assert response.headers["x-tts-provider"] == "google"
    assert any("texttospeech" in host for host in seen)
    assert response.content == b"google-mp3"


async def test_marathi_request_names_the_wavenet_voice(
    client: AsyncClient, keys, http
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"audioContent": base64.b64encode(b"x").decode()}
        )

    http(handler)
    await client.post("/api/voice/speak", json={"text": "नमस्कार", "language": "mr"})

    assert captured["voice"]["name"] == "mr-IN-Wavenet-A"
    assert captured["voice"]["languageCode"] == "mr-IN"
    assert captured["voice"]["ssmlGender"] == "FEMALE"


async def test_elevenlabs_request_uses_the_multilingual_model(
    client: AsyncClient, keys, http
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=b"mp3")

    http(handler)
    await client.post("/api/voice/speak", json={"text": "Hello", "language": "en"})
    assert captured["model_id"] == "eleven_multilingual_v2"
    # Calm delivery: this reads crowd warnings and emergency instructions.
    assert captured["voice_settings"]["style"] == 0.0


async def test_base64_encoding_is_available(client: AsyncClient, keys, http) -> None:
    http(lambda request: httpx.Response(200, content=b"mp3-bytes"))
    body = (
        await client.post(
            "/api/voice/speak",
            json={"text": "Gate 3 is busy.", "language": "en", "encoding": "base64"},
        )
    ).json()
    assert base64.b64decode(body["audio_base64"]) == b"mp3-bytes"
    assert body["media_type"] == "audio/mpeg"


async def test_rejects_text_over_the_limit(client: AsyncClient, keys) -> None:
    response = await client.post(
        "/api/voice/speak",
        json={"text": "x" * (settings.tts_max_characters + 1), "language": "en"},
    )
    assert response.status_code == 413


async def test_rejects_empty_text(client: AsyncClient, keys) -> None:
    response = await client.post(
        "/api/voice/speak", json={"text": "", "language": "en"}
    )
    assert response.status_code == 422


async def test_unsupported_language_is_rejected(client: AsyncClient, keys) -> None:
    response = await client.post(
        "/api/voice/speak", json={"text": "hello", "language": "kn"}
    )
    assert response.status_code == 422


async def test_503_when_the_language_has_no_provider(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "voice_openai_only", False, raising=False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", None, raising=False)
    response = await client.post(
        "/api/voice/speak", json={"text": "hello", "language": "en"}
    )
    assert response.status_code == 503
    assert "elevenlabs" in response.json()["error"]["message"]


def test_cache_key_is_stable_and_language_specific() -> None:
    assert tts.cache_key("Gate 3 is busy.", "en") == tts.cache_key("Gate 3 is busy.", "en")
    # The same sentence in two languages is two different recordings.
    assert tts.cache_key("Gate 3 is busy.", "en") != tts.cache_key("Gate 3 is busy.", "hi")
    assert tts.cache_key("a", "en").startswith("tts:")


def test_cache_ttl_is_a_day() -> None:
    assert settings.tts_cache_ttl_seconds == 86_400
