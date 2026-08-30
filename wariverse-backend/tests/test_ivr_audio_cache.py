"""Static IVR prompt warming, and retry-safety for a turn.

Two features that only show up under bad conditions: the cache saves latency and
money on the first call after a restart, and turn replay stops a client on a
flaky connection from advancing the menu twice for one tap.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import ivr_audio_cache, ivr_state, tts

pytestmark = pytest.mark.anyio


# --- fakes ------------------------------------------------------------------


class FakeRedis:
    """Just the four operations the cache and the replay store use."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, *keys) -> int:
        return sum(bool(self.data.pop(key, None)) for key in keys)


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    """One fake store, shared by every module that reaches for Redis directly."""
    fake = FakeRedis()
    monkeypatch.setattr(ivr_audio_cache, "get_redis", lambda: fake)
    monkeypatch.setattr(tts, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def openai(monkeypatch) -> list[str]:
    """Configure OpenAI TTS and record what actually gets synthesised."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    calls: list[str] = []

    async def fake_synthesis(text: str, language: str) -> bytes:
        calls.append(text)
        return f"mp3::{text[:16]}".encode()

    monkeypatch.setattr(tts, "synthesize_openai_bytes", fake_synthesis)
    return calls


# --- the static prompt set --------------------------------------------------


def test_the_set_covers_every_fixed_prompt() -> None:
    texts = {prompt.text for prompt in ivr_audio_cache.iter_static_prompts()}

    for language in ("mr", "hi", "en"):
        assert ivr_state.menu_prompt(language) in texts
        assert ivr_state.goodbye(language) in texts
    # The trilingual opening, and the emergency confirmation.
    assert ivr_state.start().text in texts
    assert any("press one to confirm" in text.lower() for text in texts)


def test_data_backed_prompts_are_not_warmed() -> None:
    """Crowd, temple, facility and SOS answers are written per request.

    They arrive from the state machine with empty text, so nothing about them
    should reach the cache — warming a half-built sentence would be worse than
    not warming at all.
    """
    prompts = ivr_audio_cache.iter_static_prompts()
    assert all(prompt.text.strip() for prompt in prompts)

    crowd = ivr_state.next_state("menu", "1", "en")
    assert crowd.action == "crowd_summary"
    assert crowd.text == ""


def test_repeated_strings_collapse() -> None:
    """The walk visits far more transitions than there are distinct sentences."""
    walked = sum(
        1
        for language in ivr_audio_cache.LANGUAGES
        for state in ("language", "menu", "sos_confirm", "speech", "ended")
        for key in ivr_audio_cache.KEYS
        if ivr_state.next_state(state, key, language).text
    )
    prompts = ivr_audio_cache.iter_static_prompts()

    assert walked > 100
    assert len(prompts) < 25, "distinct prompts should collapse to a handful"


def test_choosing_a_language_is_tagged_with_the_language_chosen() -> None:
    """Pressing 1 at the language menu returns the *Marathi* menu.

    The router sets `state.language` from the key before it speaks, so this has
    to be cached under `mr`. Tagging it with the language the caller was on a
    moment earlier would warm a key no request ever reads.
    """
    prompts = {p.text: p.languages for p in ivr_audio_cache.iter_static_prompts()}
    marathi_menu = ivr_state.menu_prompt("mr")

    assert "mr" in prompts[marathi_menu]
    assert "en" not in prompts[marathi_menu]


def test_the_manifest_tracks_copy_and_voice(monkeypatch) -> None:
    baseline = ivr_audio_cache.manifest_hash()
    assert ivr_audio_cache.manifest_hash() == baseline, "must be stable"

    # Switching voice makes every cached clip wrong, so it has to re-warm.
    monkeypatch.setattr(settings, "openai_tts_voice", "nova", raising=False)
    assert ivr_audio_cache.manifest_hash() != baseline


# --- warming ----------------------------------------------------------------


async def test_warm_synthesises_each_string_once_and_shares_it(redis, openai) -> None:
    result = await ivr_audio_cache.warm_static_prompts()
    prompts = ivr_audio_cache.iter_static_prompts()

    assert result.ran
    assert result.failed == 0
    # The expensive assertion: one API call per distinct sentence, not one per
    # sentence *per language*.
    assert len(openai) == len(prompts) == result.synthesised
    assert len(set(openai)) == len(openai), "no string synthesised twice"

    entries = sum(len(prompt.languages) for prompt in prompts)
    assert entries > len(prompts), "some prompts are shared across languages"

    # Every language tag the router might look up is populated all the same.
    for prompt in prompts:
        for language in prompt.languages:
            key = tts.cache_key(prompt.text, language, tts.OPENAI_PROVIDER)
            assert key in redis.data


async def test_static_prompts_outlive_ordinary_replies(redis, openai) -> None:
    await ivr_audio_cache.warm_static_prompts()

    key = tts.cache_key(ivr_state.menu_prompt("en"), "en", tts.OPENAI_PROVIDER)
    assert redis.ttls[key] == settings.ivr_static_audio_ttl_seconds
    assert redis.ttls[key] > settings.tts_cache_ttl_seconds


async def test_a_second_warm_does_no_work(redis, openai) -> None:
    await ivr_audio_cache.warm_static_prompts()
    calls_after_first = len(openai)

    again = await ivr_audio_cache.warm_static_prompts()

    assert again.skipped == "already_current"
    assert len(openai) == calls_after_first, "a restart must not re-buy the audio"


async def test_a_changed_prompt_triggers_a_re_warm(redis, openai, monkeypatch) -> None:
    await ivr_audio_cache.warm_static_prompts()
    monkeypatch.setattr(settings, "openai_tts_voice", "shimmer", raising=False)

    assert (await ivr_audio_cache.warm_static_prompts()).ran


async def test_only_one_worker_warms(redis, openai) -> None:
    """`--workers 4` must not mean four times the API bill on every deploy."""
    redis.data[ivr_audio_cache.LOCK_KEY] = "held-by-another-worker"

    result = await ivr_audio_cache.warm_static_prompts()

    assert result.skipped == "locked"
    assert openai == []


async def test_a_partial_warm_is_retried_next_time(redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)

    async def flaky(text: str, language: str) -> bytes:
        if ivr_state.goodbye("en") in text:
            raise RuntimeError("openai is having a moment")
        return b"mp3"

    monkeypatch.setattr(tts, "synthesize_openai_bytes", flaky)
    result = await ivr_audio_cache.warm_static_prompts()

    assert result.failed >= 1
    # One bad prompt does not abandon the rest...
    assert result.synthesised >= 1
    # ...and the run is not recorded as complete, so the next boot tries again.
    assert ivr_audio_cache.MANIFEST_KEY not in redis.data


async def test_a_rate_limit_is_waited_out_not_given_up_on(
    redis, monkeypatch
) -> None:
    """Warming asks for two dozen clips at once, so 429 is routine.

    Observed live: the startup warm consumed a modest account's per-minute quota
    and live `/api/voice/speak` calls started failing behind it.
    """
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(ivr_audio_cache, "RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    attempts: dict[str, int] = {}

    async def rate_limited_once(text: str, language: str) -> bytes:
        attempts[text] = attempts.get(text, 0) + 1
        if attempts[text] == 1:
            raise RuntimeError("openai tts 429: rate limit reached")
        return b"mp3"

    monkeypatch.setattr(tts, "synthesize_openai_bytes", rate_limited_once)
    result = await ivr_audio_cache.warm_static_prompts()

    assert result.failed == 0, "a 429 must be retried, not counted as a loss"
    assert result.synthesised == result.prompts
    assert all(count == 2 for count in attempts.values())


async def test_the_providers_own_retry_delay_is_used(redis, monkeypatch) -> None:
    """OpenAI says "try again in 20s"; guessing 2s just spends another request."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(ivr_audio_cache.asyncio, "sleep", record_sleep)

    once: dict[str, int] = {}

    async def rate_limited_once(text: str, language: str) -> bytes:
        once[text] = once.get(text, 0) + 1
        if once[text] == 1:
            raise tts.SynthesisError("openai tts 429: rate limit", retry_after=20.0)
        return b"mp3"

    monkeypatch.setattr(tts, "synthesize_openai_bytes", rate_limited_once)
    await ivr_audio_cache.warm_static_prompts()

    assert slept, "a rate limit must be waited out"
    # Within the jitter band around 20s, not the 2s default.
    assert all(17.0 <= s <= 25.0 for s in slept), slept


async def test_a_sustained_rate_limit_abandons_the_warm(redis, monkeypatch) -> None:
    """The cache is an optimisation; the live call is the product.

    On OpenAI's unpaid tier `tts-1` allows three requests a minute. Grinding
    through two dozen prompts holds the quota at zero for minutes while pilgrims
    wait on `/api/voice/speak`, so the warm yields instead.
    """
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    # Bind the real sleep before patching: the lambda would otherwise call the
    # patched name and recurse into itself.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(ivr_audio_cache.asyncio, "sleep", lambda _: real_sleep(0))
    calls: list[str] = []

    async def always_limited(text: str, language: str) -> bytes:
        calls.append(text)
        raise tts.SynthesisError("openai tts 429: rate limit reached", retry_after=20.0)

    monkeypatch.setattr(tts, "synthesize_openai_bytes", always_limited)
    result = await ivr_audio_cache.warm_static_prompts()

    assert result.skipped == "rate_limited"
    # It gives up rather than attempting all 22 prompts four times each.
    assert len(calls) < result.prompts, f"{len(calls)} calls for {result.prompts} prompts"
    assert ivr_audio_cache.MANIFEST_KEY not in redis.data, "must re-warm once quota returns"


async def test_a_permanent_error_is_not_retried_forever(redis, monkeypatch) -> None:
    """Only rate limits are worth waiting on; a bad request never improves."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    calls: list[str] = []

    async def always_400(text: str, language: str) -> bytes:
        calls.append(text)
        raise RuntimeError("openai tts 400: invalid voice")

    monkeypatch.setattr(tts, "synthesize_openai_bytes", always_400)
    result = await ivr_audio_cache.warm_static_prompts()

    assert result.failed == result.prompts
    assert len(calls) == result.prompts, "one attempt each, no retry storm"


async def test_warming_is_skipped_without_a_key(redis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    assert (
        await ivr_audio_cache.warm_static_prompts()
    ).skipped == "openai_not_configured"


async def test_warming_is_skipped_without_redis(monkeypatch) -> None:
    """Nowhere to store the result means synthesising would just burn money."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(ivr_audio_cache, "get_redis", lambda: None)

    assert (
        await ivr_audio_cache.warm_static_prompts()
    ).skipped == "redis_unavailable"


# --- turn replay ------------------------------------------------------------


@pytest.fixture
def replay_store(monkeypatch) -> FakeRedis:
    from app.routers import ivr_session

    fake = FakeRedis()
    monkeypatch.setattr(ivr_session, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def session_id() -> str:
    return f"ivr-{uuid4().hex[:12]}"


async def test_a_retried_keypress_is_not_applied_twice(
    client: AsyncClient, session_id: str, replay_store
) -> None:
    """The case this exists for: the answer was lost, so the client sends again.

    Pressing 3 at the language menu picks English. Applied a second time — now
    from the service menu — the same key would mean "nearby facilities", so a
    client that simply retried would skip a menu level it never saw.
    """
    await client.post(
        "/api/ivr/session/start", json={"session_id": session_id}
    )
    body = {"session_id": session_id, "key": "3", "turn_id": "turn-1"}

    first = await client.post("/api/ivr/session/dtmf", json=body)
    second = await client.post("/api/ivr/session/dtmf", json=body)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert second.json()["state"] == "menu"
    assert second.json()["language"] == "en"


async def test_a_fresh_id_is_a_fresh_press(
    client: AsyncClient, session_id: str, replay_store
) -> None:
    """Two genuine presses of the same key must both count."""
    await client.post(
        "/api/ivr/session/start", json={"session_id": session_id, "language": "en"}
    )

    first = await client.post(
        "/api/ivr/session/dtmf",
        json={"session_id": session_id, "key": "4", "turn_id": "turn-a"},
    )
    second = await client.post(
        "/api/ivr/session/dtmf",
        json={"session_id": session_id, "key": "0", "turn_id": "turn-b"},
    )

    assert first.json()["state"] == "sos_confirm"
    # A different id, so it advances rather than replaying the confirmation.
    assert second.json()["state"] == "menu"


async def test_a_retried_emergency_confirmation_does_not_dispatch_twice(
    client: AsyncClient, session_id: str, replay_store
) -> None:
    """The reason replay protection is not merely a nicety.

    A retried "1" at the SOS confirmation would otherwise be re-read from the
    menu it lands back on, quietly turning a confirmed emergency into a crowd
    report — and any repeat that *did* land on the confirmation would raise a
    second event for one person.
    """
    await client.post(
        "/api/ivr/session/start", json={"session_id": session_id, "language": "en"}
    )
    await client.post(
        "/api/ivr/session/dtmf",
        json={"session_id": session_id, "key": "4", "turn_id": "turn-a"},
    )
    confirm = {"session_id": session_id, "key": "1", "turn_id": "turn-b"}

    first = await client.post("/api/ivr/session/dtmf", json=confirm)
    second = await client.post("/api/ivr/session/dtmf", json=confirm)

    assert second.json() == first.json()
    assert [w["type"] for w in second.json()["widgets"]] == ["sos"]


async def test_turns_without_an_id_still_work(
    client: AsyncClient, session_id: str, replay_store
) -> None:
    """`turn_id` is optional — an older client keeps working, unprotected."""
    await client.post(
        "/api/ivr/session/start", json={"session_id": session_id, "language": "en"}
    )
    response = await client.post(
        "/api/ivr/session/dtmf", json={"session_id": session_id, "key": "0"}
    )

    assert response.status_code == 200
    assert replay_store.data == {}
