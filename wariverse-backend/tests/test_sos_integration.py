"""SOS against real Postgres and Redis: persistence, pub/sub, admin lifecycle.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.db_models import SosEvent
from app.services.sos_service import SOS_CHANNEL
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)

API_KEY = "integration-admin-key"


def a_session() -> str:
    return f"sos-session-{random.randint(0, 10**9)}"


def trigger_body(**overrides) -> dict:
    return {
        "session_id": a_session(),
        "latitude": 17.6790,
        "longitude": 75.3245,
        "channel": "app",
        **overrides,
    }


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def _event(sos_id: str) -> SosEvent:
    async with await _db() as db:
        return (
            await db.execute(select(SosEvent).where(SosEvent.id == sos_id))
        ).scalar_one()


async def _clear_chat_rate_limit(key: str) -> None:
    from app.redis_client import get_redis

    client = get_redis()
    if client is not None:
        await client.delete(f"wv:chat:rate:{key}")


# --- persistence ------------------------------------------------------------


async def test_event_is_persisted_and_activated(live_client: AsyncClient) -> None:
    body = trigger_body()
    response = await live_client.post(
        "/api/sos/trigger", json=body, params={"language": "en"}
    )
    assert response.status_code == 201

    payload = response.json()
    assert payload["status"] == "ACTIVATED"
    # The dashboard was reachable, so say so honestly.
    assert payload["control_room_status"] == "Connected"

    event = await _event(payload["sos_id"])
    assert event.status == "ACTIVATED"
    assert event.latitude == pytest.approx(17.6790)
    assert event.channel == "app"
    assert event.session_id is not None
    assert event.resolved_at is None


async def test_channel_is_recorded(live_client: AsyncClient) -> None:
    payload = (
        await live_client.post("/api/sos/trigger", json=trigger_body(channel="ivr"))
    ).json()
    assert (await _event(payload["sos_id"])).channel == "ivr"


async def test_notes_capture_the_dispatch_detail(live_client: AsyncClient) -> None:
    payload = (
        await live_client.post(
            "/api/sos/trigger",
            json=trigger_body(emergency_type="medical", description="collapsed"),
        )
    ).json()

    notes = (await _event(payload["sos_id"])).notes
    assert "type=medical" in notes
    assert "dispatched_to=" in notes
    assert "description=collapsed" in notes


async def test_anonymous_trigger_gets_a_session(live_client: AsyncClient) -> None:
    payload = (
        await live_client.post(
            "/api/sos/trigger", json={"latitude": 17.679, "longitude": 75.3245}
        )
    ).json()
    # sos_events.session_id is NOT NULL, so one is created rather than refusing.
    assert (await _event(payload["sos_id"])).session_id is not None


# --- pub/sub ----------------------------------------------------------------


async def test_full_event_is_published_to_sos_new(live_client: AsyncClient) -> None:
    from app.redis_client import get_redis

    pubsub = get_redis().pubsub()
    await pubsub.subscribe(SOS_CHANNEL)
    try:
        payload = (
            await live_client.post(
                "/api/sos/trigger", json=trigger_body(emergency_type="medical")
            )
        ).json()

        message = None
        for _ in range(40):
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.05
            )
            if message:
                break
            await asyncio.sleep(0.05)

        assert message is not None, "the dashboard never received the emergency"
        event = json.loads(message["data"])

        assert event["sos_id"] == payload["sos_id"]
        assert event["status"] == "ACTIVATED"
        assert event["latitude"] == pytest.approx(17.6790)
        assert event["channel"] == "app"
        assert event["emergency_type"] == "medical"
        # A responder needs somewhere to go and someone to call.
        assert event["dispatched_to"]
        assert event["eta_minutes"] > 0
        assert event["timestamp"].endswith(("AM", "PM"))
    finally:
        await pubsub.unsubscribe(SOS_CHANNEL)
        await pubsub.aclose()


async def test_row_exists_before_the_event_is_announced(
    live_client: AsyncClient, monkeypatch
) -> None:
    """The durable record must precede the announcement.

    If publishing came first, a crash between the two would leave the dashboard
    showing an emergency with no row behind it.
    """
    seen: dict[str, str | None] = {}

    from app.services import sos_service

    original = sos_service.SosService._publish

    async def spy(self, event, emergency_type, desk, eta, created_at):
        async with await _db() as db:
            row = (
                await db.execute(select(SosEvent).where(SosEvent.id == event.id))
            ).scalar_one_or_none()
            seen["status_at_publish"] = row.status if row else None
        return await original(self, event, emergency_type, desk, eta, created_at)

    monkeypatch.setattr(sos_service.SosService, "_publish", spy)
    await live_client.post("/api/sos/trigger", json=trigger_body())

    assert seen["status_at_publish"] == "PENDING"


async def test_a_failed_publish_still_activates(
    live_client: AsyncClient, monkeypatch
) -> None:
    """Pub/sub is a push optimisation; the poll endpoint reads Postgres.

    Leaving the event PENDING would make a delivered emergency look
    undispatched on the dashboard.
    """
    import app.redis_client as redis_client

    healthy = redis_client._healthy
    redis_client._healthy = False
    try:
        response = await live_client.post("/api/sos/trigger", json=trigger_body())
        assert response.status_code == 201

        payload = response.json()
        assert payload["status"] == "ACTIVATED"
        # ...but the card does not claim the control room is connected.
        assert payload["control_room_status"] != "Connected"
        assert (await _event(payload["sos_id"])).status == "ACTIVATED"
    finally:
        redis_client._healthy = healthy


async def test_an_unannounced_event_still_reaches_the_dashboard(
    live_client: AsyncClient, monkeypatch
) -> None:
    import app.redis_client as redis_client
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)

    healthy = redis_client._healthy
    redis_client._healthy = False
    try:
        payload = (
            await live_client.post("/api/sos/trigger", json=trigger_body())
        ).json()
    finally:
        redis_client._healthy = healthy

    active = (
        await live_client.get(
            "/api/admin/sos/active", headers={"X-API-Key": API_KEY}
        )
    ).json()
    assert payload["sos_id"] in [e["sos_id"] for e in active]


# --- admin lifecycle --------------------------------------------------------


async def test_active_list_shows_unresolved_events(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    payload = (await live_client.post("/api/sos/trigger", json=trigger_body())).json()

    response = await live_client.get(
        "/api/admin/sos/active", headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 200

    events = response.json()
    mine = next(e for e in events if e["sos_id"] == payload["sos_id"])
    assert set(mine) == {
        "sos_id", "status", "session_id", "latitude", "longitude",
        "channel", "notes", "created_at", "resolved_at", "age_minutes",
    }
    assert mine["status"] == "ACTIVATED"
    assert mine["age_minutes"] >= 0


async def test_active_list_is_newest_first(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    for _ in range(3):
        await live_client.post("/api/sos/trigger", json=trigger_body())

    events = (
        await live_client.get("/api/admin/sos/active", headers={"X-API-Key": API_KEY})
    ).json()
    timestamps = [e["created_at"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_resolving_removes_it_from_the_active_list(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    payload = (await live_client.post("/api/sos/trigger", json=trigger_body())).json()

    resolved = await live_client.post(
        f"/api/sos/{payload['sos_id']}/resolve",
        json={"status": "RESOLVED", "note": "Pilgrim reached the medical post"},
        headers={"X-API-Key": API_KEY},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["resolved_at"] is not None
    assert "reached the medical post" in resolved.json()["notes"]

    active = (
        await live_client.get("/api/admin/sos/active", headers={"X-API-Key": API_KEY})
    ).json()
    assert payload["sos_id"] not in [e["sos_id"] for e in active]


async def test_resolve_works_without_a_body(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    payload = (await live_client.post("/api/sos/trigger", json=trigger_body())).json()

    response = await live_client.post(
        f"/api/sos/{payload['sos_id']}/resolve", headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"


async def test_status_can_be_moved_explicitly(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    payload = (await live_client.post("/api/sos/trigger", json=trigger_body())).json()

    response = await live_client.post(
        f"/api/sos/{payload['sos_id']}/update-status",
        json={"status": "PENDING", "note": "Awaiting volunteer assignment"},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


async def test_reopening_clears_the_resolved_timestamp(
    live_client: AsyncClient, monkeypatch
) -> None:
    """A reopened emergency must not read as already closed."""
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    payload = (await live_client.post("/api/sos/trigger", json=trigger_body())).json()
    headers = {"X-API-Key": API_KEY}

    await live_client.post(f"/api/sos/{payload['sos_id']}/resolve", headers=headers)
    reopened = await live_client.post(
        f"/api/sos/{payload['sos_id']}/update-status",
        json={"status": "ACTIVATED", "note": "Reported again"},
        headers=headers,
    )
    assert reopened.json()["resolved_at"] is None

    active = (await live_client.get("/api/admin/sos/active", headers=headers)).json()
    assert payload["sos_id"] in [e["sos_id"] for e in active]


async def test_unknown_sos_id_returns_404(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings
    from uuid import uuid4

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    response = await live_client.post(
        f"/api/sos/{uuid4()}/resolve", headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 404


# --- the widget flow --------------------------------------------------------


async def test_confirm_flow_creates_a_real_event(live_client: AsyncClient) -> None:
    """Steps 1-4 of the documented frontend flow, end to end."""
    key = a_session()
    await _clear_chat_rate_limit(key)

    # 1. The pilgrim reports an emergency; the model asks for confirmation.
    raised = (
        await live_client.post(
            "/api/conversation/message",
            json={
                "session_id": key,
                "message": "help me, my mother collapsed",
                "language": "en",
                "latitude": 17.6790,
                "longitude": 75.3245,
            },
        )
    ).json()
    assert raised["widgets"][0]["data"]["status"] == "CONFIRMATION_REQUIRED"

    # 2-3. The pilgrim taps confirm, which triggers the SOS internally.
    confirmed = (
        await live_client.post(
            "/api/conversation/sos/confirm",
            json={"session_id": key, "language": "en"},
        )
    ).json()

    # 4. The card comes back activated.
    data = confirmed["widgets"][0]["data"]
    assert data["status"] == "ACTIVATED"
    assert data["control_room_status"] == "Connected"
    assert data["timestamp"].endswith(("AM", "PM"))

    # ...and a real row exists behind it.
    async with await _db() as db:
        from app.models.db_models import Session

        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one()
        events = (
            (
                await db.execute(
                    select(SosEvent).where(SosEvent.session_id == session.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert events[0].status == "ACTIVATED"


async def test_cancelling_creates_no_event(live_client: AsyncClient) -> None:
    key = a_session()
    await _clear_chat_rate_limit(key)

    await live_client.post(
        "/api/conversation/message",
        json={
            "session_id": key,
            "message": "help me, emergency",
            "language": "en",
            "latitude": 17.6790,
            "longitude": 75.3245,
        },
    )
    await live_client.post(
        "/api/conversation/sos/confirm",
        json={"session_id": key, "language": "en", "confirmed": False},
    )

    async with await _db() as db:
        from app.models.db_models import Session

        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one_or_none()
        events = (
            (
                await db.execute(
                    select(SosEvent).where(SosEvent.session_id == session.id)
                )
            )
            .scalars()
            .all()
            if session
            else []
        )
    # Declining must not leave a phantom emergency on the dashboard.
    assert events == []
