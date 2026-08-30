"""TTS caching against a real Redis.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import base64
import uuid

import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import tts
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key", raising=False)
    monkeypatch.setattr(settings, "google_tts_api_key", "g-key", raising=False)
    # Exercises the multi-provider path; production is OpenAI-only.
    monkeypatch.setattr(settings, "voice_openai_only", False, raising=False)


@pytest.fixture
def counting_http(monkeypatch):
    """Count how many times a provider is actually called."""

    calls: list[str] = []

    def install(audio: bytes = b"ID3-generated-mp3"):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "texttospeech" in request.url.host:
                return httpx.Response(
                    200, json={"audioContent": base64.b64encode(audio).decode()}
                )
            return httpx.Response(200, content=audio)

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        return calls

    return install


def a_phrase() -> str:
    return f"Gate 3 is busy. {uuid.uuid4().hex}"


async def test_second_request_is_served_from_cache(
    live_client: AsyncClient, keys, counting_http
) -> None:
    """The assistant repeats itself constantly; re-synthesising is money burnt."""
    calls = counting_http()
    text = a_phrase()

    first = await live_client.post(
        "/api/voice/speak", json={"text": text, "language": "en"}
    )
    assert first.status_code == 200
    assert first.headers["x-tts-cached"] == "miss"
    assert len(calls) == 1

    second = await live_client.post(
        "/api/voice/speak", json={"text": text, "language": "en"}
    )
    assert second.headers["x-tts-cached"] == "hit"
    assert second.content == first.content
    # The provider was not called again.
    assert len(calls) == 1


async def test_cache_entry_expires_after_a_day(
    live_client: AsyncClient, keys, counting_http
) -> None:
    from app.redis_client import get_redis

    counting_http()
    text = a_phrase()
    await live_client.post("/api/voice/speak", json={"text": text, "language": "en"})

    ttl = await get_redis().ttl(tts.cache_key(text, "en"))
    assert 0 < ttl <= settings.tts_cache_ttl_seconds


async def test_the_same_sentence_in_two_languages_is_cached_separately(
    live_client: AsyncClient, keys, counting_http
) -> None:
    calls = counting_http()
    text = a_phrase()

    await live_client.post("/api/voice/speak", json={"text": text, "language": "en"})
    await live_client.post("/api/voice/speak", json={"text": text, "language": "mr"})

    # Different voice, different provider, different recording.
    assert len(calls) == 2
    assert any("elevenlabs" in url for url in calls)
    assert any("texttospeech" in url for url in calls)


async def test_streaming_and_base64_share_one_cache_entry(
    live_client: AsyncClient, keys, counting_http
) -> None:
    calls = counting_http(b"shared-audio")
    text = a_phrase()

    streamed = await live_client.post(
        "/api/voice/speak", json={"text": text, "language": "en"}
    )
    encoded = await live_client.post(
        "/api/voice/speak",
        json={"text": text, "language": "en", "encoding": "base64"},
    )

    assert base64.b64decode(encoded.json()["audio_base64"]) == streamed.content
    assert encoded.json()["cached"] is True
    assert len(calls) == 1


async def test_cached_audio_survives_a_round_trip_intact(
    live_client: AsyncClient, keys, counting_http
) -> None:
    # Binary is base64-encoded into Redis; bytes must come back identical.
    audio = bytes(range(256)) * 4
    counting_http(audio)
    text = a_phrase()

    await live_client.post("/api/voice/speak", json={"text": text, "language": "en"})
    assert await tts.cached(text, "en") == audio


async def test_a_failed_synthesis_is_not_cached(
    live_client: AsyncClient, keys, monkeypatch
) -> None:
    """A provider error must not poison the cache with a truncated clip."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="provider down")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    text = a_phrase()
    await live_client.post("/api/voice/speak", json={"text": text, "language": "en"})
    assert await tts.cached(text, "en") is None
