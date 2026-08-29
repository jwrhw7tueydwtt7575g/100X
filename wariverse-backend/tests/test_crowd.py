"""Crowd density endpoints, the daily curve, and the admin override."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from app.data.reference import ZONES_BY_ID
from app.services.crowd_service import (
    HOURLY_CURVE,
    ZONE_INTENSITY,
    CrowdService,
    curve_density,
    density_to_status,
    format_hour,
)
from app.services.crowd_simulator import MAX_STEP, next_density
from app.utils import IST

ZONES = ["gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road"]

CROWD_FIELDS = {
    "zone_id",
    "zone_name",
    "density",
    "status",
    "latitude",
    "longitude",
    "updated_at",
}


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=IST)


# --- GET /api/crowd/{zone_id} -----------------------------------------------


async def test_zone_response_matches_the_documented_shape(client: AsyncClient) -> None:
    body = (await client.get("/api/crowd/gate-3", params={"language": "en"})).json()

    assert set(body) == CROWD_FIELDS
    assert body["zone_id"] == "gate-3"
    assert body["zone_name"] == "Gate 3"
    assert 0 <= body["density"] <= 100
    assert body["status"] in ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
    assert isinstance(body["latitude"], float)
    # A rendered phrase the app prints as-is, not an ISO timestamp.
    assert "T" not in body["updated_at"]


async def test_density_and_status_always_agree(client: AsyncClient) -> None:
    for zone_id in ZONES:
        body = (await client.get(f"/api/crowd/{zone_id}")).json()
        assert body["status"] == density_to_status(body["density"]), zone_id


async def test_zone_name_is_localized(client: AsyncClient) -> None:
    marathi = (await client.get("/api/crowd/gate-3", params={"language": "mr"})).json()
    assert marathi["zone_name"] == "दरवाजा ३"
    assert "ago" not in marathi["updated_at"]


async def test_unknown_zone_returns_404_listing_the_real_ones(client: AsyncClient) -> None:
    response = await client.get("/api/crowd/does-not-exist")
    assert response.status_code == 404
    assert "gate-1" in response.json()["error"]["message"]


# --- GET /api/crowd/all -----------------------------------------------------


async def test_all_returns_every_zone(client: AsyncClient) -> None:
    response = await client.get("/api/crowd/all")
    assert response.status_code == 200

    body = response.json()
    assert isinstance(body, list)
    assert [z["zone_id"] for z in body] == ZONES
    for zone in body:
        assert set(zone) == CROWD_FIELDS


async def test_all_is_not_swallowed_by_the_zone_route(client: AsyncClient) -> None:
    # `/all` is declared before `/{zone_id}`; if that order were lost, "all"
    # would be read as a zone id and 404.
    assert (await client.get("/api/crowd/all")).status_code == 200


# --- GET /api/crowd/{zone_id}/forecast --------------------------------------


async def test_forecast_shape(client: AsyncClient) -> None:
    body = (
        await client.get("/api/crowd/gate-3/forecast", params={"language": "en"})
    ).json()

    assert set(body) == {"zone_id", "zone_name", "points", "recommendation", "updated_at"}
    assert len(body["points"]) == 12
    assert body["updated_at"].startswith("Updated")

    for point in body["points"]:
        assert set(point) == {"time", "value"}
        assert 0 <= point["value"] <= 100
        assert point["time"].endswith(("AM", "PM"))


async def test_forecast_recommendation_names_the_zone(client: AsyncClient) -> None:
    body = (
        await client.get("/api/crowd/gate-3/forecast", params={"language": "en"})
    ).json()
    assert "Gate 3" in body["recommendation"]


async def test_forecast_starts_from_the_current_reading(client: AsyncClient) -> None:
    now = (await client.get("/api/crowd/temple-main")).json()
    forecast = (await client.get("/api/crowd/temple-main/forecast")).json()
    # The first point must not contradict the density on screen.
    assert abs(forecast["points"][0]["value"] - now["density"]) <= 2


async def test_forecast_hours_can_be_narrowed(client: AsyncClient) -> None:
    body = (await client.get("/api/crowd/gate-1/forecast", params={"hours": 4})).json()
    assert len(body["points"]) == 4


async def test_forecast_unknown_zone_returns_404(client: AsyncClient) -> None:
    assert (await client.get("/api/crowd/nowhere/forecast")).status_code == 404


# --- the daily curve --------------------------------------------------------


def test_curve_peaks_between_10am_and_2pm() -> None:
    by_hour = {hour: curve_density("temple-main", at(hour)) for hour in range(24)}
    busiest = max(by_hour, key=lambda h: by_hour[h])
    assert 10 <= busiest <= 14

    # The whole 10-14 window should be busier than early morning or late night.
    peak = min(by_hour[h] for h in range(10, 15))
    assert peak > by_hour[3]
    assert peak > by_hour[23]


def test_curve_is_quietest_overnight() -> None:
    by_hour = {hour: curve_density("gate-1", at(hour)) for hour in range(24)}
    assert min(by_hour, key=lambda h: by_hour[h]) in (1, 2, 3)


def test_curve_interpolates_between_hours() -> None:
    # Half past should sit between the two neighbouring hours, not jump.
    lower = curve_density("gate-1", at(9))
    middle = curve_density("gate-1", at(9, 30))
    upper = curve_density("gate-1", at(10))
    assert lower <= middle <= upper


def test_curve_stays_in_range_for_every_zone_and_hour() -> None:
    for zone_id in ZONES:
        for hour in range(24):
            assert 0 <= curve_density(zone_id, at(hour)) <= 100


def test_ghat_peaks_earlier_than_the_temple() -> None:
    # Bathing happens at dawn; darshan peaks mid-morning.
    ghat = max(range(24), key=lambda h: curve_density("bhima-ghat", at(h)))
    temple = max(range(24), key=lambda h: curve_density("temple-main", at(h)))
    assert ghat < temple


def test_busier_zones_read_higher_at_the_same_moment() -> None:
    moment = at(11)
    assert curve_density("temple-main", moment) > curve_density("main-road", moment)
    assert ZONE_INTENSITY["temple-main"] > ZONE_INTENSITY["main-road"]


def test_curve_covers_all_24_hours() -> None:
    assert set(HOURLY_CURVE) == set(range(24))


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, "12 AM"), (8, "8 AM"), (11, "11 AM"), (12, "12 PM"), (13, "1 PM"), (23, "11 PM")],
)
def test_hour_formatting(hour: int, expected: str) -> None:
    assert format_hour(at(hour)) == expected


# --- the simulator ----------------------------------------------------------


def test_simulator_stays_near_the_curve() -> None:
    baseline = curve_density("gate-2")
    samples = [next_density("gate-2", None) for _ in range(50)]
    assert all(abs(s - baseline) <= 6 for s in samples)
    assert all(0 <= s <= 100 for s in samples)


def test_simulator_moves_gradually_from_the_previous_reading() -> None:
    # A crowd builds and thins; a 40-point jump would be a sensor fault.
    for previous in (0, 20, 50, 90, 100):
        for _ in range(20):
            assert abs(next_density("temple-main", previous) - previous) <= MAX_STEP


def test_simulator_output_is_always_a_valid_density() -> None:
    for zone_id in ZONES:
        for _ in range(20):
            value = next_density(zone_id, None)
            assert 0 <= value <= 100
            assert density_to_status(value) in ("LOW", "MODERATE", "HIGH", "VERY_HIGH")


# --- admin override ---------------------------------------------------------


async def test_admin_refuses_when_no_key_is_configured(client: AsyncClient) -> None:
    # Fail closed: a misconfigured deploy must not publish an open write endpoint.
    response = await client.post("/api/admin/crowd/gate-1", json={"density": 90})
    assert response.status_code == 503


async def test_admin_rejects_a_wrong_key(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        "/api/admin/crowd/gate-1",
        json={"density": 90},
        headers={"X-API-Key": "not-the-key"},
    )
    assert response.status_code == 401


async def test_admin_rejects_a_missing_key(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post("/api/admin/crowd/gate-1", json={"density": 90})
    assert response.status_code == 401


async def test_admin_rejects_an_out_of_range_density(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        "/api/admin/crowd/gate-1",
        json={"density": 140},
        headers={"X-API-Key": "the-real-key"},
    )
    assert response.status_code == 422


async def test_admin_needs_the_reading_store(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.post(
        "/api/admin/crowd/gate-1",
        json={"density": 90},
        headers={"X-API-Key": "the-real-key"},
    )
    # An override that cannot be stored would silently vanish on the next tick.
    assert response.status_code == 503


# --- service ----------------------------------------------------------------


async def test_read_all_covers_every_zone() -> None:
    readings = await CrowdService(None).read_all()
    assert [r.zone_id for r in readings] == ZONES
    assert all(r.source == "model" for r in readings)


async def test_forecast_values_are_bounded() -> None:
    points = await CrowdService(None).forecast("gate-2", hours=24)
    assert len(points) == 24
    assert all(0 <= p["value"] <= 100 for p in points)
    assert all(p["status"] == density_to_status(p["value"]) for p in points)


async def test_forecast_rejects_an_unknown_zone() -> None:
    from app.services.crowd_service import ZoneNotFoundError

    with pytest.raises(ZoneNotFoundError):
        await CrowdService(None).forecast("atlantis")


async def test_recommendation_tells_a_busy_pilgrim_when_to_return() -> None:
    service = CrowdService(None)
    busy_then_quiet = [
        {"time": "12 PM", "value": 90, "status": "VERY_HIGH"},
        {"time": "1 PM", "value": 80, "status": "HIGH"},
        {"time": "2 PM", "value": 40, "status": "MODERATE"},
    ]
    text = service.recommendation("gate-3", busy_then_quiet, "en")
    assert "after 2 pm" in text.lower()
    assert "Gate 3" in text


async def test_recommendation_names_the_first_easing_not_the_quietest_hour() -> None:
    """"Come back at 2 AM" is useless advice to someone queueing at 4 PM."""
    service = CrowdService(None)
    points = [
        {"time": "4 PM", "value": 76, "status": "HIGH"},
        {"time": "5 PM", "value": 69, "status": "HIGH"},
        {"time": "6 PM", "value": 55, "status": "MODERATE"},  # first relief
        {"time": "7 PM", "value": 44, "status": "MODERATE"},
        {"time": "2 AM", "value": 7, "status": "LOW"},  # global minimum
    ]
    text = service.recommendation("gate-3", points, "en")
    assert "6 PM" in text
    assert "2 AM" not in text


async def test_recommendation_tells_a_quiet_pilgrim_to_go_now() -> None:
    service = CrowdService(None)
    quiet_then_busy = [
        {"time": "8 AM", "value": 38, "status": "MODERATE"},
        {"time": "9 AM", "value": 50, "status": "MODERATE"},
        {"time": "10 AM", "value": 62, "status": "HIGH"},
    ]
    text = service.recommendation("gate-3", quiet_then_busy, "en")
    assert "Before 10 AM" in text


async def test_recommendation_when_it_never_gets_busy() -> None:
    service = CrowdService(None)
    calm = [{"time": f"{h} AM", "value": 20, "status": "LOW"} for h in range(1, 5)]
    assert "comfortable" in service.recommendation("main-road", calm, "en")


def test_every_spec_zone_is_registered() -> None:
    assert list(ZONES_BY_ID) == ZONES


def test_every_zone_has_a_curve_intensity() -> None:
    assert set(ZONE_INTENSITY) == set(ZONES_BY_ID)


def test_humanize_age_phrases() -> None:
    from app.utils import humanize_age, now_utc

    now = now_utc()
    assert humanize_age(now, "en", now=now) == "just now"
    assert humanize_age(now - timedelta(minutes=2), "en", now=now) == "2 min ago"
    assert humanize_age(now - timedelta(hours=3), "en", now=now) == "3 hr ago"
    assert humanize_age(now - timedelta(days=2), "en", now=now) == "2 days ago"
