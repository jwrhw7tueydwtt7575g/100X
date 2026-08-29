"""Text to speech: ElevenLabs for English/Hindi, Google WaveNet for Marathi.

ElevenLabs' multilingual model handles Marathi poorly enough that a pilgrim
would hear their own language mispronounced, so `mr` goes to Google's
`mr-IN-Wavenet-A` instead.

Output is cached in Redis under `tts:{sha256(text|language)}` for 24 hours.
The assistant repeats itself constantly — "Gate 3 is busy", the SOS
acknowledgement, the darshan timings — and re-synthesising those on every
request is money spent to produce a byte-identical file.

Streaming: chunks go to the caller as they arrive *and* accumulate into a
buffer that is written to the cache when the stream completes. A caller who
hangs up mid-stream simply leaves the cache unpopulated.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator

import httpx
import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

CACHE_PREFIX = "tts:"
MEDIA_TYPE = "audio/mpeg"

# Which provider owns which language.
ELEVENLABS_LANGUAGES = ("en", "hi")
GOOGLE_LANGUAGES = ("mr",)


class SynthesisError(RuntimeError):
    """No provider could produce audio for this request."""


def cache_key(text: str, language: str, provider: str = "") -> str:
    """Cache key for a clip.

    `provider` is empty for the language's default provider, which keeps the
    historical key shape so existing cache entries stay valid. The in-app IVR
    passes `openai`, giving its differently-voiced audio its own namespace
    rather than colliding with ElevenLabs output for the same sentence.
    """
    material = f"{text}|{language}|{provider}" if provider else f"{text}|{language}"
    return f"{CACHE_PREFIX}{hashlib.sha256(material.encode()).hexdigest()}"


def provider_for(language: str) -> str:
    return "google" if language in GOOGLE_LANGUAGES else "elevenlabs"


def is_configured(language: str) -> bool:
    if provider_for(language) == "google":
        return bool(
            settings.google_tts_api_key or settings.google_application_credentials
        )
    return bool(settings.elevenlabs_api_key)


# --- cache ------------------------------------------------------------------


async def cached(text: str, language: str, provider: str = "") -> bytes | None:
    """Previously synthesised audio, if any.

    Stored base64-encoded because the shared Redis client decodes responses as
    UTF-8 text; a second binary-mode connection pool for this one use would
    cost more than the 33% encoding overhead on a ~40 KB clip.
    """
    client = get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(cache_key(text, language, provider))
    except (RedisError, OSError) as exc:
        log.warning("tts_cache_read_failed", error=str(exc))
        return None
    if not raw:
        return None
    try:
        return base64.b64decode(raw)
    except (ValueError, TypeError):
        log.warning("tts_cache_corrupt", language=language)
        return None


async def store(
    text: str,
    language: str,
    audio: bytes,
    provider: str = "",
    ttl: int | None = None,
) -> None:
    """Cache a clip. `ttl` overrides the default for prompts that rarely change."""
    client = get_redis()
    if client is None or not audio:
        return
    try:
        await client.set(
            cache_key(text, language, provider),
            base64.b64encode(audio).decode("ascii"),
            ex=ttl if ttl is not None else settings.tts_cache_ttl_seconds,
        )
    except (RedisError, OSError) as exc:
        log.warning("tts_cache_write_failed", error=str(exc))


# --- synthesis --------------------------------------------------------------


async def synthesize(text: str, language: str) -> AsyncIterator[bytes]:
    """Yield MP3 chunks, serving the cache when it has them.

    The buffer written to the cache is only committed once the provider stream
    finishes, so a truncated download never becomes a permanently truncated
    cache entry.
    """
    if hit := await cached(text, language):
        log.info("tts_cache_hit", language=language, bytes=len(hit))
        yield hit
        return

    provider = provider_for(language)
    if not is_configured(language):
        raise SynthesisError(f"{provider} is not configured for language {language}")

    log.info("tts_synthesising", language=language, provider=provider,
             characters=len(text))

    chunks: list[bytes] = []
    stream = (
        _google(text, language) if provider == "google" else _elevenlabs(text, language)
    )
    async for chunk in stream:
        chunks.append(chunk)
        yield chunk

    await store(text, language, b"".join(chunks))


async def synthesize_bytes(text: str, language: str) -> bytes:
    """The whole clip at once — for callers that need it base64-encoded."""
    return b"".join([chunk async for chunk in synthesize(text, language)])


OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_PROVIDER = "openai"


def openai_configured() -> bool:
    return bool(settings.openai_api_key)


async def synthesize_openai(text: str, language: str) -> AsyncIterator[bytes]:
    """OpenAI `tts-1`, used by the in-app IVR.

    Kept separate from `synthesize()` so the existing `/api/voice/speak`
    routing (ElevenLabs for en/hi, Google WaveNet for mr) is untouched.

    ⚠️ OpenAI's voices are English-accented. `tts-1` will *read* Marathi and
    Hindi text, but it pronounces place names noticeably worse than the
    Google WaveNet voice `/api/voice/speak` uses for `mr`. If IVR audio quality
    in Marathi matters, point this at `tts.synthesize()` instead — the menu
    prompts are short and heavily cached either way.
    """
    if hit := await cached(text, language, OPENAI_PROVIDER):
        log.info("tts_cache_hit", language=language, provider=OPENAI_PROVIDER,
                 bytes=len(hit))
        yield hit
        return

    if not openai_configured():
        raise SynthesisError("OPENAI_API_KEY is not set")

    log.info(
        "tts_synthesising",
        language=language,
        provider=OPENAI_PROVIDER,
        model=settings.openai_tts_model,
        characters=len(text),
    )

    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        async with client.stream(
            "POST",
            OPENAI_TTS_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_tts_model,
                "voice": settings.openai_tts_voice,
                "input": text,
                "response_format": "mp3",
            },
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")[:200]
                raise SynthesisError(f"openai tts {response.status_code}: {body}")
            async for chunk in response.aiter_bytes():
                if chunk:
                    chunks.append(chunk)
                    yield chunk

    # Committed only once the stream finishes, so a dropped connection cannot
    # leave a truncated clip cached forever.
    await store(text, language, b"".join(chunks), OPENAI_PROVIDER)


async def synthesize_openai_bytes(text: str, language: str) -> bytes:
    return b"".join([chunk async for chunk in synthesize_openai(text, language)])


async def _elevenlabs(text: str, language: str) -> AsyncIterator[bytes]:
    url = ELEVENLABS_URL.format(voice_id=settings.elevenlabs_voice_id)
    payload = {
        "text": text,
        "model_id": settings.elevenlabs_model,
        "voice_settings": {
            # Higher stability and low style: this reads crowd warnings and
            # emergency instructions, where an expressive delivery would be
            # actively unhelpful.
            "stability": 0.6,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        async with client.stream(
            "POST",
            url,
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": settings.elevenlabs_api_key or "",
                "accept": MEDIA_TYPE,
            },
            json=payload,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")[:200]
                raise SynthesisError(f"elevenlabs {response.status_code}: {body}")
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk


async def _google(text: str, language: str) -> AsyncIterator[bytes]:
    """Google returns the whole clip base64-encoded — no chunked transfer."""
    body = {
        "input": {"text": text},
        "voice": {
            "languageCode": "mr-IN",
            "name": settings.google_tts_voice_mr,
            "ssmlGender": "FEMALE",
        },
        "audioConfig": {"audioEncoding": "MP3"},
    }

    params: dict[str, str] = {}
    headers: dict[str, str] = {}
    if settings.google_tts_api_key:
        params["key"] = settings.google_tts_api_key
    else:
        headers["Authorization"] = f"Bearer {await _google_access_token()}"

    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        response = await client.post(
            GOOGLE_TTS_URL, params=params, headers=headers, json=body
        )
    if response.status_code != 200:
        raise SynthesisError(f"google tts {response.status_code}: {response.text[:200]}")

    encoded = response.json().get("audioContent")
    if not encoded:
        raise SynthesisError("google tts returned no audio")
    yield base64.b64decode(encoded)


# --- Google service-account auth --------------------------------------------

_token_cache: dict[str, float | str] = {}


async def _google_access_token() -> str:
    """Mint an access token from the service-account JSON.

    Hand-rolled rather than pulling in `google-auth`: it is one signed JWT
    exchanged for a token, and `python-jose` (already a dependency for our own
    JWTs) can do the RS256 signing.
    """
    now = time.time()
    if _token_cache.get("token") and float(_token_cache.get("expires_at", 0)) > now + 60:
        return str(_token_cache["token"])

    path = settings.google_application_credentials
    if not path:
        raise SynthesisError("GOOGLE_APPLICATION_CREDENTIALS is not set")

    try:
        with open(path, encoding="utf-8") as handle:
            account = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SynthesisError(f"could not read service account: {exc}") from exc

    from jose import jwt

    assertion = jwt.encode(
        {
            "iss": account["client_email"],
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "aud": GOOGLE_TOKEN_URL,
            "iat": int(now),
            "exp": int(now) + 3600,
        },
        account["private_key"],
        algorithm="RS256",
    )

    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
    if response.status_code != 200:
        raise SynthesisError(f"google token {response.status_code}: {response.text[:200]}")

    payload = response.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = now + int(payload.get("expires_in", 3600))
    return str(payload["access_token"])
