"""Route guidance: contract, route selection, and congestion avoidance."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.route_service import (
    ROUTES,
    ROUTES_BY_ID,
    TEMPLE_LAT,
    TEMPLE_LON,
    WALKING_SPEED_KMPH,
    RouteService,
    format_duration,
    path_length_m,
)

FIELDS = {
    "origin",
    "destination",
    "route_coordinates",
    "estimated_time",
    "distance",
    "avoid_areas",
}


async def guidance(client: AsyncClient, **params) -> dict:
    params.setdefault("origin_lat", 17.6790)
    params.setdefault("origin_lng", 75.3245)
    params.setdefault("language", "en")
    response = await client.get("/api/routes/guidance", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- contract ---------------------------------------------------------------


async def test_matches_the_route_widget_shape(client: AsyncClient) -> None:
    body = await guidance(client)

    assert set(body) == FIELDS
    assert set(body["origin"]) == {"latitude", "longitude", "label"}
    assert set(body["destination"]) == {"latitude", "longitude", "label"}
    assert set(body["route_coordinates"][0]) == {"latitude", "longitude"}
    # Rendered strings, printed verbatim by the app.
    assert body["estimated_time"].endswith("walk")
    assert body["distance"].endswith("km")


async def test_destination_defaults_to_the_temple(client: AsyncClient) -> None:
    body = await guidance(client)
    assert body["destination"]["latitude"] == pytest.approx(TEMPLE_LAT)
    assert body["destination"]["longitude"] == pytest.approx(TEMPLE_LON)
    assert body["destination"]["label"] == "Vitthal Temple"


async def test_origin_is_echoed_and_labelled(client: AsyncClient) -> None:
    body = await guidance(client, origin_lat=17.6800, origin_lng=75.3250)
    assert body["origin"]["latitude"] == pytest.approx(17.6800)
    assert body["origin"]["label"] == "Current location"


async def test_route_starts_at_the_origin_and_ends_at_the_destination(
    client: AsyncClient,
) -> None:
    body = await guidance(client, origin_lat=17.6805, origin_lng=75.3240)
    coordinates = body["route_coordinates"]

    assert coordinates[0]["latitude"] == pytest.approx(17.6805)
    assert coordinates[-1]["latitude"] == pytest.approx(TEMPLE_LAT)
    assert len(coordinates) >= 3, "a route needs intermediate points to draw"


async def test_explicit_destination_is_honoured(client: AsyncClient) -> None:
    body = await guidance(client, dest_lat=17.6812, dest_lng=75.3262)
    assert body["destination"]["latitude"] == pytest.approx(17.6812)
    # A known place gets named rather than left anonymous.
    assert body["destination"]["label"] == "Bhima Ghat"


async def test_labels_are_localized(client: AsyncClient) -> None:
    body = await guidance(client, language="mr")
    assert body["origin"]["label"] == "सध्याचे ठिकाण"
    assert "मिनिटे" in body["estimated_time"]


# --- walking speed ----------------------------------------------------------


def test_walking_speed_is_2_point_5_kmph() -> None:
    # Crowd pace, not open-road pace.
    assert WALKING_SPEED_KMPH == 2.5


@pytest.mark.parametrize(
    ("metres", "expected"),
    [(1200, "29 min walk"), (2500, "60 min walk"), (100, "2 min walk")],
)
def test_duration_uses_the_crowd_walking_speed(metres: int, expected: str) -> None:
    assert format_duration(metres) == expected


async def test_estimated_time_matches_the_distance(client: AsyncClient) -> None:
    body = await guidance(client)
    km = float(body["distance"].split()[0])
    minutes = int(body["estimated_time"].split()[0])
    assert minutes == pytest.approx(km / WALKING_SPEED_KMPH * 60, abs=2)


# --- the three routes -------------------------------------------------------


def test_there_are_three_precomputed_routes() -> None:
    assert len(ROUTES) == 3
    assert set(ROUTES_BY_ID) == {"east-gate-1", "north-gate-2", "south-gate-3"}


def test_every_route_ends_at_the_temple() -> None:
    for route in ROUTES:
        last = route["coordinates"][-1]
        assert last == (TEMPLE_LAT, TEMPLE_LON), route["route_id"]


def test_every_route_passes_through_known_zones() -> None:
    from app.data.reference import ZONES_BY_ID

    for route in ROUTES:
        assert route["zones"], route["route_id"]
        assert set(route["zones"]) <= set(ZONES_BY_ID), route["route_id"]


def test_routes_have_plausible_lengths() -> None:
    for route in ROUTES:
        metres = path_length_m(route["coordinates"])
        # Approaches within the temple precinct: hundreds of metres, not tens
        # of kilometres.
        assert 200 < metres < 5000, route["route_id"]


# --- congestion-aware selection ---------------------------------------------


async def test_a_congested_route_is_avoided(monkeypatch) -> None:
    """Gate 3 jammed should push guidance onto another approach."""
    service = RouteService(None)

    async def statuses(self):
        return {
            "gate-1": "LOW",
            "gate-2": "LOW",
            "gate-3": "VERY_HIGH",
            "temple-main": "MODERATE",
            "bhima-ghat": "LOW",
            "main-road": "LOW",
        }

    monkeypatch.setattr(RouteService, "_zone_statuses", statuses)
    route = await service.guidance(17.6765, 75.3320, language="en")
    assert route.route_id != "south-gate-3"


async def test_congested_zones_are_listed_to_avoid(monkeypatch) -> None:
    service = RouteService(None)

    async def statuses(self):
        return {
            "gate-1": "LOW",
            "gate-2": "LOW",
            "gate-3": "HIGH",
            "temple-main": "VERY_HIGH",
            "bhima-ghat": "LOW",
            "main-road": "LOW",
        }

    monkeypatch.setattr(RouteService, "_zone_statuses", statuses)
    route = await service.guidance(17.6790, 75.3245, language="en")

    assert "Gate 3 — high congestion" in route.avoid_areas
    assert "Main Temple — very high congestion" in route.avoid_areas
    assert len(route.avoid_areas) == 2


async def test_no_avoid_areas_when_everything_is_calm(monkeypatch) -> None:
    service = RouteService(None)

    async def statuses(self):
        return {z: "LOW" for z in
                ("gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road")}

    monkeypatch.setattr(RouteService, "_zone_statuses", statuses)
    route = await service.guidance(17.6790, 75.3245, language="en")
    assert route.avoid_areas == []


async def test_a_route_is_always_returned_even_when_all_are_congested(
    monkeypatch,
) -> None:
    """"No route" helps nobody — pick the least bad and say what to avoid."""
    service = RouteService(None)

    async def statuses(self):
        return {z: "VERY_HIGH" for z in
                ("gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road")}

    monkeypatch.setattr(RouteService, "_zone_statuses", statuses)
    route = await service.guidance(17.6790, 75.3245, language="en")

    assert route.route_coordinates
    assert route.route_id in ROUTES_BY_ID
    assert len(route.avoid_areas) == 6


async def test_nearby_origin_picks_the_closer_approach(monkeypatch) -> None:
    service = RouteService(None)

    async def statuses(self):
        return {z: "LOW" for z in
                ("gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road")}

    monkeypatch.setattr(RouteService, "_zone_statuses", statuses)

    # Standing at the ghat, the north approach should win on distance.
    north = await service.guidance(17.6812, 75.3262, language="en")
    assert north.route_id == "north-gate-2"
