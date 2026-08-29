"""Emergency SOS: request contract, admin guards, and degraded behaviour.

The full trigger → persist → publish → activate sequence runs against real
Postgres and Redis in `test_sos_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.services.sos_service import SOS_CHANNEL, UNRESOLVED, SosService
from app.utils import format_clock

TEMPLE_LAT = 17.6775
TEMPLE_LON = 75.3283

TRIGGER = {
    "session_id": "wariverse-session",
    "latitude": 17.6790,
    "longitude": 75.3245,
    "channel": "app",
}

FIELDS = {"sos_id", "status", "message", "control_room_status", "timestamp"}


# --- trigger contract -------------------------------------------------------


async def test_trigger_matches_the_documented_response(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger", json=TRIGGER, params={"language": "en"}
    )
    assert response.status_code == 201

    body = response.json()
    assert set(body) == FIELDS
    assert body["status"] == "ACTIVATED"
    assert body["message"] == (
        "Help has been requested. Stay calm. A volunteer is being notified."
    )
    assert body["timestamp"].endswith(("AM", "PM"))
    assert body["sos_id"]


async def test_trigger_works_without_a_database(client: AsyncClient) -> None:
    """An emergency must never 500.

    Returning an error to someone whose relative has collapsed helps nobody —
    the failure is logged for reconciliation and the pilgrim still gets an
    acknowledgement.
    """
    response = await client.post("/api/sos/trigger", json=TRIGGER)
    assert response.status_code == 201
    assert response.json()["status"] == "ACTIVATED"


async def test_trigger_is_unauthenticated(client: AsyncClient) -> None:
    # A pilgrim who never registered must still be able to call for help.
    response = await client.post("/api/sos/trigger", json=TRIGGER)
    assert response.status_code == 201


async def test_trigger_accepts_an_invalid_token_without_failing(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/sos/trigger",
        json=TRIGGER,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 201


async def test_session_id_is_optional(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger",
        json={"latitude": TEMPLE_LAT, "longitude": TEMPLE_LON},
    )
    assert response.status_code == 201


async def test_coordinates_are_required(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger", json={"session_id": "wariverse-session"}
    )
    assert response.status_code == 422


async def test_old_field_names_are_rejected(client: AsyncClient) -> None:
    # The contract is latitude/longitude; lat/lon must not silently work.
    response = await client.post(
        "/api/sos/trigger", json={"lat": TEMPLE_LAT, "lon": TEMPLE_LON}
    )
    assert response.status_code == 422


async def test_camelcase_is_accepted(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger",
        json={"sessionId": "wariverse-session", "latitude": 17.679, "longitude": 75.3245},
    )
    assert response.status_code == 201


@pytest.mark.parametrize("channel", ["app", "ivr"])
async def test_both_channels_are_accepted(client: AsyncClient, channel: str) -> None:
    response = await client.post(
        "/api/sos/trigger", json={**TRIGGER, "channel": channel}
    )
    assert response.status_code == 201


async def test_unknown_channel_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger", json={**TRIGGER, "channel": "carrier-pigeon"}
    )
    assert response.status_code == 422


async def test_out_of_range_coordinates_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger", json={**TRIGGER, "latitude": 200}
    )
    assert response.status_code == 422


async def test_message_is_localized(client: AsyncClient) -> None:
    body = (
        await client.post("/api/sos/trigger", json=TRIGGER, params={"language": "mr"})
    ).json()
    # A pilgrim in an emergency must understand the acknowledgement.
    assert "मदत" in body["message"]


async def test_control_room_status_is_honest_when_the_dashboard_is_unreachable(
    client: AsyncClient,
) -> None:
    """Without Redis the dashboard was never told, so do not claim "Connected"."""
    body = (
        await client.post("/api/sos/trigger", json=TRIGGER, params={"language": "en"})
    ).json()
    assert body["control_room_status"] == "Standing by"


# --- admin guards -----------------------------------------------------------


async def test_resolve_requires_an_api_key(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(f"/api/sos/{uuid4()}/resolve")
    assert response.status_code == 401


async def test_update_status_requires_an_api_key(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        f"/api/sos/{uuid4()}/update-status", json={"status": "RESOLVED"}
    )
    assert response.status_code == 401


async def test_active_list_requires_an_api_key(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.get("/api/admin/sos/active")
    assert response.status_code == 401


async def test_admin_endpoints_fail_closed_without_a_configured_key(
    client: AsyncClient,
) -> None:
    # No ADMIN_API_KEY set: refuse, never fall open.
    assert (await client.get("/api/admin/sos/active")).status_code == 503
    assert (await client.post(f"/api/sos/{uuid4()}/resolve")).status_code == 503


async def test_update_status_rejects_an_unknown_status(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        f"/api/sos/{uuid4()}/update-status",
        json={"status": "MAYBE"},
        headers={"X-API-Key": "the-real-key"},
    )
    assert response.status_code == 422


async def test_admin_operations_need_the_store(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        f"/api/sos/{uuid4()}/resolve", headers={"X-API-Key": "the-real-key"}
    )
    assert response.status_code == 503


# --- service ----------------------------------------------------------------


def test_publishes_to_the_documented_channel() -> None:
    assert SOS_CHANNEL == "sos:new"


def test_unresolved_covers_everything_before_resolution() -> None:
    # A PENDING event — persisted but never announced — must still be listed;
    # it is exactly the case a human needs to notice.
    assert set(UNRESOLVED) == {"PENDING", "ACTIVATED"}


def test_eta_falls_back_when_no_responder_is_near() -> None:
    assert SosService._eta_minutes(None) == 15


def test_notes_carry_the_dispatch_detail() -> None:
    notes = SosService._notes(
        "medical", "Wari Medical Center", 4, "+919876543210", "collapsed", 12.4
    )
    assert "type=medical" in notes
    assert "dispatched_to=Wari Medical Center" in notes
    assert "eta_minutes=4" in notes
    assert "callback=+919876543210" in notes
    assert "gps_accuracy_m=12" in notes


def test_helplines_lead_with_the_national_number() -> None:
    assert SosService.helplines()[0] == "112"
    assert "108" in SosService.helplines()


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(10, 30, "10:30 AM"), (0, 5, "12:05 AM"), (12, 0, "12:00 PM"), (23, 59, "11:59 PM")],
)
def test_clock_formatting(hour: int, minute: int, expected: str) -> None:
    # Built in IST, which is what a responder on the ground reads.
    from app.utils import IST

    moment = datetime(2026, 7, 15, hour, minute, tzinfo=IST)
    assert format_clock(moment) == expected


def test_clock_converts_from_utc() -> None:
    # 05:00 UTC is 10:30 IST.
    assert format_clock(datetime(2026, 7, 15, 5, 0, tzinfo=UTC)) == "10:30 AM"
