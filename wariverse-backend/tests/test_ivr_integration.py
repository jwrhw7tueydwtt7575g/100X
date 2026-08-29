"""A whole IVR call against real Postgres and Redis.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models.db_models import Message, Session, SosEvent
from app.routers.ivr import ESCALATION_CHANNEL
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)

AUTH_TOKEN = "test-twilio-auth-token"
BASE_URL = "http://test"


@pytest.fixture
def call_sid() -> str:
    """A CallSid unique across runs.

    Sessions are keyed by CallSid and persist in Postgres, so a per-run counter
    would reuse ids from the previous run and accumulate messages against them.
    """
    return f"CA{uuid4().hex}"


@pytest.fixture
def twilio(monkeypatch):
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


async def post(client: AsyncClient, twilio, path: str, **params) -> ElementTree.Element:
    response = await client.post(path, data=params, headers=twilio(path, params))
    assert response.status_code == 200, response.text
    return ElementTree.fromstring(response.text)


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def _session(call_sid: str) -> Session:
    async with await _db() as db:
        return (
            await db.execute(select(Session).where(Session.session_token == call_sid))
        ).scalar_one()


async def _messages(session_id) -> list[Message]:
    async with await _db() as db:
        return list(
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )


# --- a whole call -----------------------------------------------------------


async def test_a_full_call_is_transcribed_under_an_ivr_session(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(live_client, twilio, "/api/ivr/voice/answer", CallSid=call_sid)
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3"
    )
    await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="How crowded is Gate 3?",
    )
    await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="When is the best time to visit?",
    )
    await post(
        live_client, twilio, "/api/ivr/voice/status",
        CallSid=call_sid, CallStatus="completed", CallDuration="94",
        From="+919876543210",
    )

    session = await _session(call_sid)
    assert session.channel == "ivr"
    assert session.language == "en"
    # Call metadata is recorded when the call ends.
    assert session.context_json["call"]["call_status"] == "completed"
    assert session.context_json["call"]["duration_seconds"] == 94

    messages = await _messages(session.id)
    assert [m.role for m in messages] == [
        "user", "assistant", "user", "assistant",
    ]
    assert messages[0].content == "How crowded is Gate 3?"
    # The pilgrim's turns came from speech.
    assert messages[0].is_voice is True
    assert all(m.language == "en" for m in messages)


async def test_transcript_survives_a_call_that_never_completes(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    """Turns are written as they happen, not at call end.

    A dropped call would otherwise lose the whole conversation — including an
    emergency mentioned in it.
    """
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3"
    )
    await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="Where is the nearest water point?",
    )
    # No /status webhook — the caller's battery died.

    session = await _session(call_sid)
    messages = await _messages(session.id)
    assert len(messages) == 2
    assert messages[0].content == "Where is the nearest water point?"


async def test_the_call_keeps_its_language_across_turns(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="1"
    )
    root = await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="गर्दी किती आहे?",
    )
    assert root.find("Say").get("language") == "mr-IN"
    assert root.find("Say").get("voice") == "Google.mr-IN-Standard-A"

    session = await _session(call_sid)
    assert session.language == "mr"


async def test_spoken_location_is_remembered_for_later_turns(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3"
    )
    await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="I am standing near Bhima Ghat",
    )

    session = await _session(call_sid)
    location = session.context_json["last_location"]
    # Pinned to the ghat, so "nearest facility" now means something.
    assert location["lat"] == pytest.approx(17.6812, abs=0.001)


# --- emergency --------------------------------------------------------------


async def test_digit_nine_creates_a_real_ivr_sos(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3"
    )
    await post(
        live_client, twilio, "/api/ivr/voice/dtmf",
        CallSid=call_sid, Digits="9", From="+919876543210",
    )

    session = await _session(call_sid)
    async with await _db() as db:
        events = (
            (await db.execute(select(SosEvent).where(SosEvent.session_id == session.id)))
            .scalars()
            .all()
        )

    assert len(events) == 1
    event = events[0]
    assert event.status == "ACTIVATED"
    assert event.channel == "ivr"
    # No GPS on a phone call — the record still exists, with a callback number.
    assert event.latitude is None
    assert "callback=+919876543210" in event.notes
    assert "location=unknown" in event.notes


async def test_an_ivr_emergency_reaches_the_dashboard(
    live_client: AsyncClient, twilio, call_sid: str, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "admin_api_key", "integration-admin-key", raising=False)

    await post(
        live_client, twilio, "/api/ivr/voice/dtmf",
        CallSid=call_sid, Digits="9", From="+919876543210",
    )

    active = (
        await live_client.get(
            "/api/admin/sos/active", headers={"X-API-Key": "integration-admin-key"}
        )
    ).json()
    ivr_events = [e for e in active if e["channel"] == "ivr"]
    assert ivr_events, "the control room must see phone emergencies too"


async def test_a_located_caller_gets_a_dispatch_and_eta(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        live_client, twilio, "/api/ivr/voice/language", CallSid=call_sid, Digits="3"
    )
    # Saying where they are turns an unlocated emergency into a dispatchable one.
    await post(
        live_client, twilio, "/api/ivr/voice/transcribe",
        CallSid=call_sid, SpeechResult="I am at Bhima Ghat",
    )
    await post(
        live_client, twilio, "/api/ivr/voice/dtmf",
        CallSid=call_sid, Digits="9", From="+919876543210",
    )

    session = await _session(call_sid)
    async with await _db() as db:
        event = (
            (await db.execute(select(SosEvent).where(SosEvent.session_id == session.id)))
            .scalars()
            .one()
        )
    assert event.latitude is not None
    assert "eta_minutes=" in event.notes


# --- escalation -------------------------------------------------------------


async def test_digit_zero_notifies_the_dashboard(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    from app.redis_client import get_redis

    pubsub = get_redis().pubsub()
    await pubsub.subscribe(ESCALATION_CHANNEL)
    try:
        await post(
            live_client, twilio, "/api/ivr/voice/dtmf",
            CallSid=call_sid, Digits="0", From="+919876543210",
        )

        message = None
        for _ in range(40):
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.05
            )
            if message:
                break
            await asyncio.sleep(0.05)

        assert message is not None, "no volunteer was told a caller is waiting"
        payload = json.loads(message["data"])
        assert payload["call_sid"] == call_sid
        assert payload["channel"] == "ivr"
    finally:
        await pubsub.unsubscribe(ESCALATION_CHANNEL)
        await pubsub.aclose()


async def test_escalation_is_recorded_on_the_session(
    live_client: AsyncClient, twilio, call_sid: str
) -> None:
    await post(
        live_client, twilio, "/api/ivr/voice/dtmf", CallSid=call_sid, Digits="0"
    )
    session = await _session(call_sid)
    assert session.context_json["escalation"]["status"] == "WAITING"
