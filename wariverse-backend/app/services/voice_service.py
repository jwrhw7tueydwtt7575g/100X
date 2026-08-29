"""`VoiceService` — the audio engine the in-app IVR talks to.

A thin facade over the existing `stt` and `tts` modules rather than a second
implementation, so `/api/voice/transcribe` and `/api/voice/speak` keep their
current provider routing untouched while the IVR gets the pairing the spec
asks for: OpenAI Whisper in, OpenAI `tts-1` out.

Speech synthesis is best-effort. If no key is configured the IVR still answers
with text and the app falls back to on-device speech — a menu that reads itself
badly is far better than a menu that will not open.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import structlog

from app.config import settings
from app.services import stt, tts

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class Spoken:
    """Audio for a prompt, plus what it says."""

    text: str
    audio_base64: str | None
    media_type: str = tts.MEDIA_TYPE
    provider: str | None = None
    cached: bool = False

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_base64)


class VoiceService:
    """Transcription and synthesis for the IVR."""

    # --- speech in ---------------------------------------------------------

    async def transcribe(
        self, audio: bytes, content_type: str, language_hint: str | None = None
    ) -> stt.Transcript:
        """Whisper first, Deepgram as the fallback.

        The reverse of `/api/voice/transcribe`, deliberately: Whisper handles
        Marathi reliably and the IVR's whole point is that a pilgrim can just
        speak. Deepgram stays in the chain so an OpenAI outage degrades to
        slower rather than silent.
        """
        return await stt.transcribe(
            audio, content_type, language_hint, prefer="whisper"
        )

    # --- speech out --------------------------------------------------------

    async def speak(self, text: str, language: str) -> Spoken:
        """Synthesise a prompt, returning base64 audio the app can play.

        Never raises: a failure here costs the audio, not the answer.
        """
        text = normalize(text)
        if not text:
            return Spoken(text="", audio_base64=None)

        if not tts.openai_configured():
            log.info("ivr_tts_unconfigured", detail="returning text only")
            return Spoken(text=text, audio_base64=None)

        try:
            was_cached = (
                await tts.cached(text, language, tts.OPENAI_PROVIDER) is not None
            )
            audio = await tts.synthesize_openai_bytes(text, language)
        except Exception as exc:  # noqa: BLE001 — the caller still gets the words
            log.error("ivr_tts_failed", language=language, error=str(exc))
            return Spoken(text=text, audio_base64=None)

        return Spoken(
            text=text,
            audio_base64=base64.b64encode(audio).decode("ascii"),
            provider=tts.OPENAI_PROVIDER,
            cached=was_cached,
        )


def normalize(text: str) -> str:
    """The exact string `speak()` will synthesise, after stripping and trimming.

    `ivr_audio_cache` warms the cache through this, so a warmed entry is
    guaranteed to sit under the key a live turn goes on to look up. Without a
    shared normalisation step the two could drift by a trailing space and the
    warm set would quietly never be hit.
    """
    text = (text or "").strip()
    if len(text) > settings.tts_max_characters:
        # Menu prompts are short; a long LLM answer gets trimmed at a sentence
        # boundary rather than cut mid-word.
        text = _trim(text, settings.tts_max_characters)
    return text


def _trim(text: str, limit: int) -> str:
    """Cut to the last sentence that fits, so speech never stops mid-word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    # Keep at least a third of the budget, so trimming never leaves a fragment
    # like "Gate" where the sentence said "Gate 3 is dangerously crowded".
    best = max(
        (window.rfind(stop) for stop in ("। ", ". ", "! ", "? ")), default=-1
    )
    if best >= limit // 3:
        return window[: best + 1].strip()
    return window.rsplit(" ", 1)[0].strip()


voice_service = VoiceService()
