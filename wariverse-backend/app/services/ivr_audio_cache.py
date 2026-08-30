"""Pre-synthesised audio for the static IVR menu prompts.

Most of what a pilgrim hears on the in-app IVR is fixed copy: the trilingual
greeting, the main menu, the "that option is not available" nudge, the SOS
confirmation, the goodbye. Those strings change only when someone edits
`app/services/ivr_state.py`, yet without warming, the first caller after every
cache expiry waits on an OpenAI round trip *in the middle of a menu*.

This module synthesises the whole static set up front and stores it under a
much longer TTL than ordinary replies get.

Three things make it cheap:

1. **The set is derived, not duplicated.** `iter_static_prompts()` walks the
   real state machine — every (state, key, language) triple — and keeps the
   transitions that carry fixed text. Reword a menu and the warm set follows
   automatically. A copy of the strings here would drift the first time someone
   edited one and nobody would notice, because the symptom is only latency.

2. **Distinct strings are synthesised once.** The walk produces ~180 reachable
   transitions but only a dozen or so distinct sentences. They are hashed and
   collapsed before any API call is made.

3. **Languages share audio.** `tts.synthesize_openai()` sends OpenAI a model, a
   voice and the text — never a language code. Two cache entries that differ
   only by language tag therefore hold byte-identical MP3s. We synthesise once
   and fan the bytes out across every language key that needs them, which is
   what makes the trilingual greeting one API call instead of three.

Warming is best-effort throughout. A failure costs latency on the first call,
never the call itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass

import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.redis_client import get_redis
from app.services import ivr_state, tts, voice_service

log = structlog.get_logger(__name__)

LANGUAGES: tuple[str, ...] = ("mr", "hi", "en")

# Every key the app can send. `next_state()` is total over these — unknown keys
# come back as an "invalid" transition rather than an error — so the walk needs
# no exception handling.
KEYS: tuple[str, ...] = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#")

MANIFEST_KEY = "ivr:audio:manifest"
LOCK_KEY = "ivr:audio:warm:lock"
# Long enough to cover a slow full warm, short enough that a crashed worker does
# not block the next deploy's attempt for long.
LOCK_TTL_SECONDS = 300

# OpenAI is comfortable with far more, but there is nothing to gain by finishing
# a background warm in four seconds instead of twelve, and a burst of parallel
# synthesis requests is exactly what trips a shared rate limit. Two, because
# three still exhausted a modest account's per-minute quota in testing and the
# live endpoint started failing behind it.
MAX_CONCURRENCY = 2

# A 429 during warming is expected, not exceptional: this asks for two dozen
# clips in a row. Wait it out rather than burning the prompt for the whole run.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = 2.0
# However long the provider asks for, never hold a background task longer than
# this — a warm that sleeps for minutes is just a slow leak of a worker.
MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class StaticPrompt:
    """One fixed prompt, and the language tags it is cached under."""

    text: str
    languages: tuple[str, ...]

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


# --- what to warm -----------------------------------------------------------


def iter_static_prompts() -> list[StaticPrompt]:
    """Every prompt the IVR can speak without consulting live data.

    Transitions whose action fetches crowd, temple, facility or SOS data arrive
    here with empty text — the router fills those in per request — so skipping
    empty text is exactly the right filter, and stays right when a new
    data-backed menu option is added.
    """
    # text -> the language tags it will be cached under.
    seen: dict[str, set[str]] = {}

    def add(text: str, language: str) -> None:
        if text := voice_service.normalize(text):
            seen.setdefault(text, set()).add(language)

    # The opening greeting, for a caller who has not picked a language yet. It
    # is trilingual, and `state.language` is whatever the session already had,
    # so it can be looked up under any of the three.
    opening = ivr_state.start()
    for language in LANGUAGES:
        add(opening.text, language)

    for language in LANGUAGES:
        # `/start` with a known language skips the language menu and lands here.
        add(ivr_state.main_menu(language, action="language_selected").text, language)

        for state in ("language", "menu", "sos_confirm", "speech", "ended"):
            for key in KEYS:
                transition = ivr_state.next_state(state, key, language)
                add(transition.text, _effective_language(transition, key, language))

    return sorted(
        (StaticPrompt(text=text, languages=tuple(sorted(langs)))
         for text, langs in seen.items()),
        key=lambda p: p.digest,
    )


def _effective_language(
    transition: ivr_state.Transition, key: str, language: str
) -> str:
    """The language tag the router will cache this transition's audio under.

    Mirrors `ivr_session.dtmf`, which sets `state.language` from the pressed key
    *before* rendering the response. Picking the language menu returns the main
    menu already written in the chosen language, so tagging it with the language
    the caller was on a moment ago would warm a key nothing ever reads.
    """
    if transition.action == "language_selected":
        return ivr_state.LANGUAGE_KEYS.get(key, language)
    return language


def manifest_hash(prompts: list[StaticPrompt] | None = None) -> str:
    """Fingerprint of the current static prompt set.

    Warming compares this against the last successful run so a restart does no
    work when the copy has not moved. Language tags are part of it: adding a
    fourth language has to trigger a re-warm even if no wording changed.
    """
    prompts = iter_static_prompts() if prompts is None else prompts
    material = "\n".join(
        f"{prompt.digest}:{','.join(prompt.languages)}" for prompt in prompts
    )
    fingerprint = hashlib.sha256(material.encode()).hexdigest()
    # Voice and model are inputs to the audio itself — switching voices must
    # invalidate the set, or callers keep hearing the old one until the TTL runs
    # out a month later.
    return hashlib.sha256(
        f"{fingerprint}|{settings.openai_tts_model}|{settings.openai_tts_voice}".encode()
    ).hexdigest()


# --- warming ----------------------------------------------------------------


@dataclass
class WarmResult:
    """What a warm run did, for logging and for tests."""

    prompts: int = 0
    synthesised: int = 0
    reused: int = 0
    failed: int = 0
    skipped: str | None = None
    # Set when a sustained rate limit ends the run. A flag rather than an
    # exception through `asyncio.gather`, which propagates the first error but
    # leaves its siblings running — they then outlive the loop and raise
    # "Event loop is closed" on the way out.
    abandoned: bool = False

    @property
    def ran(self) -> bool:
        return self.skipped is None


async def warm_static_prompts(*, force: bool = False) -> WarmResult:
    """Synthesise and cache every static prompt.

    Safe to call from several workers at once: the first to take the Redis lock
    does the work and the rest return immediately. Without that, `--workers 4`
    would quadruple the API bill on every deploy.
    """
    if not tts.openai_configured():
        log.info("ivr_audio_warm_skipped", reason="openai_not_configured")
        return WarmResult(skipped="openai_not_configured")

    client = get_redis()
    if client is None:
        # Nowhere to put the results — synthesising now would spend money to
        # throw the bytes away.
        log.info("ivr_audio_warm_skipped", reason="redis_unavailable")
        return WarmResult(skipped="redis_unavailable")

    prompts = iter_static_prompts()
    fingerprint = manifest_hash(prompts)

    try:
        if not force and await client.get(MANIFEST_KEY) == fingerprint:
            log.info("ivr_audio_warm_skipped", reason="already_current",
                     prompts=len(prompts))
            return WarmResult(prompts=len(prompts), skipped="already_current")

        if not await client.set(LOCK_KEY, fingerprint, nx=True, ex=LOCK_TTL_SECONDS):
            log.info("ivr_audio_warm_skipped", reason="another_worker_is_warming")
            return WarmResult(prompts=len(prompts), skipped="locked")
    except (RedisError, OSError) as exc:
        log.warning("ivr_audio_warm_skipped", reason="redis_error", error=str(exc))
        return WarmResult(skipped="redis_error")

    log.info(
        "ivr_audio_warm_begin",
        prompts=len(prompts),
        entries=sum(len(p.languages) for p in prompts),
        voice=settings.openai_tts_voice,
    )

    result = WarmResult(prompts=len(prompts))
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(prompt: StaticPrompt) -> None:
        # Checked here as well as inside, so prompts still queued behind the
        # semaphore never start once the run has been abandoned.
        if result.abandoned:
            return
        async with semaphore:
            if result.abandoned:
                return
            await _warm_prompt(prompt, result)

    try:
        await asyncio.gather(*(one(prompt) for prompt in prompts))

        if result.abandoned:
            result.skipped = "rate_limited"
            log.warning(
                "ivr_audio_warm_abandoned",
                reason="provider rate limit; leaving the remaining quota for live calls",
                synthesised=result.synthesised,
                remaining=result.prompts - result.synthesised - result.reused,
                detail=(
                    "OpenAI's unpaid tier allows 3 tts-1 requests per minute, "
                    "which a bulk warm cannot use without starving "
                    "/api/voice/speak. Add a payment method or set "
                    "IVR_WARM_AUDIO_ON_STARTUP=false."
                ),
            )

        if result.failed == 0 and result.skipped is None:
            # Recorded only on a clean run, so a partial warm is retried on the
            # next boot instead of being remembered as complete.
            await client.set(MANIFEST_KEY, fingerprint,
                             ex=settings.ivr_static_audio_ttl_seconds)
    except (RedisError, OSError) as exc:
        log.warning("ivr_audio_manifest_write_failed", error=str(exc))
    finally:
        try:
            await client.delete(LOCK_KEY)
        except (RedisError, OSError):
            # The TTL clears it; a stuck lock only delays the next warm.
            pass

    log.info(
        "ivr_audio_warm_complete",
        prompts=result.prompts,
        synthesised=result.synthesised,
        reused=result.reused,
        failed=result.failed,
    )
    return result


async def _warm_prompt(prompt: StaticPrompt, result: WarmResult) -> None:
    """Ensure one string is cached under every language tag that needs it."""
    primary = prompt.languages[0]

    try:
        already = await tts.cached(prompt.text, primary, tts.OPENAI_PROVIDER)
    except Exception:  # noqa: BLE001 — a cache read failure just means a miss
        already = None

    audio = already
    if audio is None:
        audio = await _synthesize_patiently(prompt, primary, result)
    else:
        result.reused += 1

    if not audio:
        return
    if already is None:
        result.synthesised += 1

    # Re-store under every tag, including the primary: `synthesize_openai()`
    # writes with the ordinary 24-hour TTL, and these deserve the long one.
    for language in prompt.languages:
        await tts.store(
            prompt.text,
            language,
            audio,
            tts.OPENAI_PROVIDER,
            ttl=settings.ivr_static_audio_ttl_seconds,
        )


async def _synthesize_patiently(
    prompt: StaticPrompt, language: str, result: WarmResult
) -> bytes | None:
    """Synthesise one prompt, backing off when the provider says slow down.

    Warming asks for the whole menu at once, which is enough to exhaust the
    per-minute limit on a modest OpenAI account — observed live, where the warm
    consumed the quota and live `/api/voice/speak` calls started failing behind
    it. Backing off keeps a background nicety from starving the thing pilgrims
    are actually waiting on.
    """
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return await tts.synthesize_openai_bytes(prompt.text, language)
        except Exception as exc:  # noqa: BLE001 — one bad prompt must not stop the rest
            rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()

            if rate_limited and attempt < RATE_LIMIT_RETRIES:
                # Prefer the provider's own answer. OpenAI replies "try again in
                # 20s" on a 3-requests-per-minute tier; an invented 2-second
                # backoff just spends another request against the same limit.
                asked = getattr(exc, "retry_after", None)
                delay = (
                    float(asked)
                    if asked
                    else RATE_LIMIT_BACKOFF_SECONDS * (2**attempt)
                )
                # Jittered so several workers do not resynchronise.
                await asyncio.sleep(
                    min(delay, MAX_BACKOFF_SECONDS) * (0.9 + random.random() / 5)
                )
                continue

            log.warning(
                "ivr_audio_warm_failed",
                digest=prompt.digest,
                language=language,
                rate_limited=rate_limited,
                error=str(exc)[:200],
            )
            result.failed += 1
            if rate_limited:
                # Out of retries against a rate limit means the account simply
                # cannot serve a bulk warm right now. Pressing on would hold the
                # quota at zero for minutes while pilgrims wait on live calls,
                # which is precisely backwards: the cache is an optimisation,
                # the call is the product.
                result.abandoned = True
            return None
    return None


# --- startup ----------------------------------------------------------------

_task: asyncio.Task | None = None


def start() -> None:
    """Kick off warming in the background.

    Deliberately not awaited: a cold OpenAI account, a slow network or a rate
    limit must not hold up the health check. The IVR answers from the live path
    until the cache fills in behind it.
    """
    global _task

    if not settings.ivr_warm_audio_on_startup:
        return
    if _task is not None and not _task.done():
        return

    _task = asyncio.create_task(_run())


async def _run() -> None:
    try:
        await warm_static_prompts()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a background task dying silently is worse
        log.exception("ivr_audio_warm_crashed")


async def stop() -> None:
    global _task

    if _task is None or _task.done():
        _task = None
        return

    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
