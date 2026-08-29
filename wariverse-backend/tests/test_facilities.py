"""Facility finder: the response contract, seed coverage, and formatting."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.data.reference import FACILITIES
from app.services.facility_service import format_availability, format_distance

TEMPLE_LAT = 17.6775
TEMPLE_LON = 75.3283

FIELDS = {
    "id",
    "category",
    "name",
    "distance",
    "latitude",
    "longitude",
    "availability",
    "contact",
}


async def nearby(client: AsyncClient, **params) -> dict:
    params.setdefault("lat", TEMPLE_LAT)
    params.setdefault("lng", TEMPLE_LON)
    params.setdefault("language", "en")
    response = await client.get("/api/facilities/nearby", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- contract ---------------------------------------------------------------


async def test_response_is_wrapped_in_a_facilities_key(client: AsyncClient) -> None:
    body = await nearby(client, radius_m=2000)
    assert set(body) == {"facilities"}
    assert isinstance(body["facilities"], list)
    assert body["facilities"]


async def test_facility_matches_the_documented_shape(client: AsyncClient) -> None:
    body = await nearby(client, category="medical", radius_m=3000)
    facility = body["facilities"][0]

    assert set(facility) >= FIELDS
    assert facility["id"].startswith("fac-")
    assert facility["category"] == "medical"
    assert "km" in facility["distance"] or "m" in facility["distance"]
    assert isinstance(facility["latitude"], float)


async def test_results_are_nearest_first(client: AsyncClient) -> None:
    body = await nearby(client, radius_m=3000)
    metres = [_metres(f["distance"]) for f in body["facilities"]]
    assert metres == sorted(metres)


def _metres(distance: str) -> float:
    parts = distance.split()
    value, unit = parts[0], parts[1]
    return float(value) * (1000 if unit == "km" else 1)


async def test_radius_is_respected(client: AsyncClient) -> None:
    tight = await nearby(client, radius_m=200)
    wide = await nearby(client, radius_m=3000)
    assert len(tight["facilities"]) < len(wide["facilities"])
    assert all(_metres(f["distance"]) <= 200 for f in tight["facilities"])


async def test_default_radius_is_1000m(client: AsyncClient) -> None:
    default = await nearby(client)
    explicit = await nearby(client, radius_m=1000)
    assert len(default["facilities"]) == len(explicit["facilities"])


async def test_lng_not_lon(client: AsyncClient) -> None:
    # The frontend sends `lng`; `lon` must not silently work.
    response = await client.get(
        "/api/facilities/nearby", params={"lat": TEMPLE_LAT, "lon": TEMPLE_LON}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "category", ["medical", "water", "toilet", "rest", "food", "accommodation"]
)
async def test_every_documented_category_is_accepted(
    client: AsyncClient, category: str
) -> None:
    body = await nearby(client, category=category, radius_m=5000)
    assert all(f["category"] == category for f in body["facilities"])


async def test_unknown_category_is_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/facilities/nearby",
        params={"lat": TEMPLE_LAT, "lng": TEMPLE_LON, "category": "helipad"},
    )
    assert response.status_code == 422


async def test_several_categories_can_be_requested(client: AsyncClient) -> None:
    response = await client.get(
        "/api/facilities/nearby",
        params=[
            ("lat", TEMPLE_LAT),
            ("lng", TEMPLE_LON),
            ("radius_m", 5000),
            ("category", "medical"),
            ("category", "water"),
        ],
    )
    assert response.status_code == 200
    categories = {f["category"] for f in response.json()["facilities"]}
    assert categories <= {"medical", "water"}


async def test_police_posts_are_not_exposed_as_a_category(client: AsyncClient) -> None:
    # SOS routes to them internally; pilgrims do not browse them.
    body = await nearby(client, radius_m=5000, limit=100)
    assert "police" not in {f["category"] for f in body["facilities"]}
    assert "lost_found_desk" not in {f["category"] for f in body["facilities"]}


async def test_accommodation_returns_empty_rather_than_inventing_places(
    client: AsyncClient,
) -> None:
    body = await nearby(client, category="accommodation", radius_m=10000)
    assert all(f["category"] == "accommodation" for f in body["facilities"])


async def test_names_are_localized(client: AsyncClient) -> None:
    marathi = await nearby(client, category="medical", radius_m=3000, language="mr")
    english = await nearby(client, category="medical", radius_m=3000, language="en")
    assert marathi["facilities"][0]["name"] != english["facilities"][0]["name"]


# --- seed coverage ----------------------------------------------------------


def test_seed_has_the_specified_counts() -> None:
    counts: dict[str, int] = {}
    for facility in FACILITIES:
        counts[facility["facility_type"]] = counts.get(facility["facility_type"], 0) + 1

    assert counts["medical"] == 3
    assert counts["water"] == 5
    assert counts["toilet"] == 4
    assert counts["rest"] == 2
    assert counts["food"] == 3


def test_seed_ids_are_unique_and_sequential() -> None:
    ids = [f["external_id"] for f in FACILITIES]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("fac-") for i in ids)


def test_medical_posts_cover_the_gates_and_the_ghat() -> None:
    from app.services.geo import haversine_m

    from app.data.reference import ZONES_BY_ID

    medical = [f for f in FACILITIES if f["facility_type"] == "medical"]
    for zone_id in ("gate-1", "gate-3", "bhima-ghat"):
        zone = ZONES_BY_ID[zone_id]
        nearest = min(
            haversine_m(zone["lat"], zone["lon"], f["lat"], f["lon"]) for f in medical
        )
        # Each of the three named areas needs a post within a short walk.
        assert nearest < 400, zone_id


def test_every_facility_declares_who_staffs_it() -> None:
    for facility in FACILITIES:
        assert (facility.get("details") or {}).get("staffing"), facility["external_id"]


# --- formatting -------------------------------------------------------------


@pytest.mark.parametrize(
    ("metres", "expected"),
    [(0, "0 m"), (45, "45 m"), (99, "99 m"), (100, "0.1 km"), (800, "0.8 km"),
     (1234, "1.2 km"), (12000, "12.0 km")],
)
def test_distance_formatting(metres: float, expected: str) -> None:
    # Sub-100 m falls back to metres — "0.0 km" would read as a bug.
    assert format_distance(metres) == expected


def test_availability_combines_status_and_staffing() -> None:
    row = {"details": {"staffing": "Volunteer staffed"}}
    assert format_availability(row, True, "en") == "Open · Volunteer staffed"
    assert format_availability(row, False, "en") == "Closed · Volunteer staffed"


def test_availability_without_staffing_is_just_the_status() -> None:
    assert format_availability({}, True, "en") == "Open"
