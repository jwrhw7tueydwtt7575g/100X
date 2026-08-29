"""Speech to text: Deepgram Nova-2, falling back to OpenAI Whisper.

Two providers because the pilgrim is already holding the phone up in a crowd
having said their piece — a provider outage should cost latency, not the
utterance. Deepgram is tried first (faster, cheaper); Whisper picks up whatever
it drops.

⚠️ **Marathi coverage differs between the two.** Whisper transcribes Marathi
well. Deepgram's Nova-2 language list changes over time and Marathi has not
always been on it — if `mr` is not supported for your account, Deepgram will
either return an empty transcript or mis-detect it as Hindi, and this module
will fall through to Whisper. Verify against Deepgram's current list before
launch rather than assuming.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"

SUPPORTED_LANGUAGES = ("en", "hi", "mr")

# Browsers hand MediaRecorder output over as webm/ogg — and Chrome labels an
# audio-only recording `video/webm`, which is why that is on the list.
ALLOWED_CONTENT_TYPES = {
    "audio/webm",
    "video/webm",
    "audio/ogg",
    "application/ogg",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/mpeg",
    "audio/mpga",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
}

# Whisper reports languages by English name; Deepgram by BCP-47 tag.
_LANGUAGE_NAMES = {
    "english": "en",
    "hindi": "hi",
    "marathi": "mr",
}


class TranscriptionError(RuntimeError):
    """Every configured provider failed."""


@dataclass(slots=True)
class Transcript:
    transcript: str
    language: str
    confidence: float
    provider: str
    duration_seconds: float | None = None


def normalise_language(value: str | None, default: str = "en") -> str:
    """Map whatever a provider reports onto one of the three we support."""
    if not value:
        return default
    lowered = value.strip().lower()
    if lowered in _LANGUAGE_NAMES:
        lowered = _LANGUAGE_NAMES[lowered]
    # "en-IN" → "en"
    code = lowered.split("-")[0]
    return code if code in SUPPORTED_LANGUAGES else default


async def transcribe(
    audio: bytes, content_type: str, language_hint: str | None = None
) -> Transcript:
    """Transcribe audio, trying Deepgram then Whisper."""
    errors: list[str] = []

    if settings.deepgram_api_key:
        try:
            return await _deepgram(audio, content_type, language_hint)
        except Exception as exc:  # noqa: BLE001 — fall through to the next provider
            errors.append(f"deepgram: {exc}")
            log.warning("deepgram_failed", error=str(exc))
    else:
        errors.append("deepgram: no API key")

    if settings.openai_api_key:
        try:
            return await _whisper(audio, content_type, language_hint)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"whisper: {exc}")
            log.error("whisper_failed", error=str(exc))
    else:
        errors.append("whisper: no API key")

    raise TranscriptionError("; ".join(errors))


# --- providers --------------------------------------------------------------


async def _deepgram(
    audio: bytes, content_type: str, language_hint: str | None
) -> Transcript:
    params: dict[str, Any] = {
        "model": settings.deepgram_model,
        "smart_format": "true",
        "punctuate": "true",
    }
    if language_hint in SUPPORTED_LANGUAGES:
        # An explicit hint beats detection: the pilgrim already told the app
        # which language they use.
        params["language"] = language_hint
    else:
        params["detect_language"] = "true"

    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        response = await client.post(
            DEEPGRAM_URL,
            params=params,
            headers={
                "Authorization": f"Token {settings.deepgram_api_key}",
                "Content-Type": content_type,
            },
            content=audio,
        )
    response.raise_for_status()
    payload = response.json()

    channel = (payload.get("results", {}).get("channels") or [{}])[0]
    alternative = (channel.get("alternatives") or [{}])[0]
    text = (alternative.get("transcript") or "").strip()
    if not text:
        raise TranscriptionError("deepgram returned an empty transcript")

    detected = channel.get("detected_language") or (
        channel.get("languages") or [None]
    )[0]
    return Transcript(
        transcript=text,
        language=normalise_language(detected or language_hint),
        confidence=round(float(alternative.get("confidence") or 0.0), 4),
        provider="deepgram",
        duration_seconds=payload.get("metadata", {}).get("duration"),
    )


async def _whisper(
    audio: bytes, content_type: str, language_hint: str | None
) -> Transcript:
    data: dict[str, str] = {"model": "whisper-1", "response_format": "verbose_json"}
    if language_hint in SUPPORTED_LANGUAGES:
        data["language"] = language_hint

    async with httpx.AsyncClient(timeout=settings.voice_timeout_seconds) as client:
        response = await client.post(
            WHISPER_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            data=data,
            files={"file": (_filename_for(content_type), audio, content_type)},
        )
    response.raise_for_status()
    payload = response.json()

    text = (payload.get("text") or "").strip()
    if not text:
        raise TranscriptionError("whisper returned an empty transcript")

    return Transcript(
        transcript=text,
        language=normalise_language(payload.get("language") or language_hint),
        confidence=_whisper_confidence(payload),
        provider="whisper",
        duration_seconds=payload.get("duration"),
    )


def _whisper_confidence(payload: dict[str, Any]) -> float:
    """Approximate a confidence from Whisper's per-segment log-probabilities.

    Whisper has no confidence field. `avg_logprob` is the mean log-probability
    of the tokens in a segment, so `exp()` of it is a rough per-token
    likelihood — good enough for the client to decide whether to show a
    "did you mean?" prompt, and honest about being an estimate.
    """
    segments = payload.get("segments") or []
    logprobs = [
        segment["avg_logprob"]
        for segment in segments
        if isinstance(segment.get("avg_logprob"), int | float)
    ]
    if not logprobs:
        return 0.0
    return round(min(1.0, math.exp(sum(logprobs) / len(logprobs))), 4)


def _filename_for(content_type: str) -> str:
    """Whisper infers the container from the filename extension."""
    suffixes = {
        "audio/webm": "webm",
        "video/webm": "webm",
        "audio/ogg": "ogg",
        "application/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "audio/mpeg": "mp3",
        "audio/mpga": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/flac": "flac",
    }
    return f"speech.{suffixes.get(content_type.split(';')[0].strip(), 'webm')}"
