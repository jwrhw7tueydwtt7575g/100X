"""Voice input and read-aloud for the app.

⚠️ Both endpoints spend money per call on third-party APIs and are open to
anyone who can reach them. They are **not rate limited** — the spec did not ask
for it and inventing a limit could break the client. Before this is public,
either put a limit in front of it (the pattern in `app/routers/conversation.py`
works) or require a bearer token. An unmetered STT endpoint is somebody else's
free transcription service.
"""

from __future__ import annotations

import base64
from typing import Annotated

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models.schemas import (
    SpeakBase64Response,
    SpeakRequest,
    TranscriptionResponse,
)
from app.services import stt, tts

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post(
    "/transcribe",
    response_model=TranscriptionResponse,
    summary="Transcribe recorded speech",
    responses={
        413: {"description": "Audio too large or too long"},
        415: {"description": "Unsupported audio format"},
        503: {"description": "No transcription provider is available"},
    },
)
async def transcribe(
    request: Request,
    file: Annotated[UploadFile, File(description="webm/ogg from browsers, m4a from mobile")],
    language: Annotated[
        str | None,
        Form(description="Optional hint (en/hi/mr). Omit to auto-detect."),
    ] = None,
) -> TranscriptionResponse:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in stt.ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"unsupported audio type: {content_type or 'unknown'}. "
                f"Send one of: {', '.join(sorted(stt.ALLOWED_CONTENT_TYPES))}"
            ),
        )

    audio = await file.read()
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the audio file is empty"
        )
    # Checked before any paid call — this is the guard that actually protects
    # spend, since duration is only knowable after decoding.
    if len(audio) > settings.voice_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"audio is {len(audio) // 1024} KB; the limit is "
                f"{settings.voice_max_upload_bytes // 1024 // 1024} MB"
            ),
        )

    try:
        result = await stt.transcribe(audio, content_type, language)
    except stt.TranscriptionError as exc:
        log.error("transcription_failed", error=str(exc), bytes=len(audio))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="could not transcribe the audio; please type your question instead",
        ) from exc

    # Duration is only known once a provider has decoded the audio, so this
    # rejects *after* the call was billed. The size cap above is what keeps
    # that from being exploitable.
    if (
        result.duration_seconds
        and result.duration_seconds > settings.voice_max_duration_seconds
    ):
        log.warning(
            "transcription_too_long",
            duration_seconds=result.duration_seconds,
            limit=settings.voice_max_duration_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"audio is {result.duration_seconds:.0f}s; the limit is "
                f"{settings.voice_max_duration_seconds}s"
            ),
        )

    log.info(
        "transcription_completed",
        provider=result.provider,
        language=result.language,
        confidence=result.confidence,
        duration_seconds=result.duration_seconds,
        characters=len(result.transcript),
        request_id=getattr(request.state, "request_id", None),
    )
    return TranscriptionResponse(
        transcript=result.transcript,
        language=result.language,  # type: ignore[arg-type]
        confidence=result.confidence,
        provider=result.provider,  # type: ignore[arg-type]
        duration_seconds=result.duration_seconds,
    )


@router.post(
    "/speak",
    summary="Read text aloud",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "MP3 audio"},
        413: {"description": "Text too long"},
        503: {"description": "No speech provider is configured for this language"},
    },
)
async def speak(payload: SpeakRequest):
    text = payload.text.strip()
    if len(text) > settings.tts_max_characters:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"text is {len(text)} characters; the limit is "
                f"{settings.tts_max_characters}"
            ),
        )
    if not tts.is_configured(payload.language):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"no speech provider configured for {payload.language} "
                f"({tts.provider_for(payload.language)})"
            ),
        )

    was_cached = await tts.cached(text, payload.language) is not None

    if payload.encoding == "base64":
        try:
            audio = await tts.synthesize_bytes(text, payload.language)
        except tts.SynthesisError as exc:
            raise _unavailable(exc) from exc
        # An explicit JSONResponse, because the route's response_class is
        # StreamingResponse — returning a model here would make FastAPI iterate
        # it as a stream and emit the field names.
        return JSONResponse(
            content=SpeakBase64Response(
                audio_base64=base64.b64encode(audio).decode("ascii"),
                language=payload.language,
                cached=was_cached,
            ).model_dump(mode="json")
        )

    async def stream():
        try:
            async for chunk in tts.synthesize(text, payload.language):
                yield chunk
        except tts.SynthesisError as exc:
            # The response has already begun, so the status code is committed.
            # Truncating is the only remaining signal; log loudly.
            log.error("tts_stream_failed", language=payload.language, error=str(exc))

    return StreamingResponse(
        stream(),
        media_type=tts.MEDIA_TYPE,
        headers={
            "Cache-Control": f"private, max-age={settings.tts_cache_ttl_seconds}",
            "X-TTS-Provider": tts.provider_for(payload.language),
            "X-TTS-Cached": "hit" if was_cached else "miss",
        },
    )


def _unavailable(exc: Exception) -> HTTPException:
    log.error("tts_failed", error=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="could not generate audio; please read the text instead",
    )
