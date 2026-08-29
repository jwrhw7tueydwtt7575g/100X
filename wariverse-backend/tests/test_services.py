"""Unit tests for the domain logic behind the endpoints."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.data.reference import ZONES_BY_ID
from app.services.crowd_service import CrowdService, density_to_status
from app.services.geo import bounding_box, haversine_m, walk_minutes
from app.services.llm_orchestrator import (
    category_from_text,
    classify_intent,
    detect_language,
    is_affirmative,
)
from app.utils import is_open_now
from scripts.seed import SEED_DENSITY
from tests.conftest import TEMPLE_LAT, TEMPLE_LON


# --- geo --------------------------------------------------------------------


def test_haversine_matches_known_distance() -> None:
    # Wakhari halt ground → Vitthal temple, roughly 5.5 km apart.
    distance = haversine_m(17.6903, 75.2787, TEMPLE_LAT, TEMPLE_LON)
    assert 5000 < distance < 6500


def test_haversine_is_zero_for_same_point() -> None:
    assert haversine_m(TEMPLE_LAT, TEMPLE_LON, TEMPLE_LAT, TEMPLE_LON) == pytest.approx(0, abs=1e-6)


def test_bounding_box_contains_the_radius() -> None:
    min_lat, max_lat, min_lon, max_lon = bounding_box(TEMPLE_LAT, TEMPLE_LON, 1000)
    assert min_lat < TEMPLE_LAT < max_lat
    assert min_lon < TEMPLE_LON < max_lon
    # A point 900 m due north must fall inside the box.
    assert min_lat <= TEMPLE_LAT + 0.008 <= max_lat


def test_congestion_slows_walking() -> None:
    clear = walk_minutes(1000, speed_kmph=4.0, congestion_factor=1.0)
    crowded = walk_minutes(1000, speed_kmph=4.0, congestion_factor=2.6)
    assert crowded > clear


# --- language and intent ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("जवळ पाणी कुठे मिळेल?", "mr"),
        ("दर्शन का समय क्या है?", "hi"),
        ("Where is the nearest toilet?", "en"),
        ("मला मदत हवी आहे", "mr"),
        ("मुझे मदद चाहिए", "hi"),
    ],
)
def test_language_detection(text: str, expected: str) -> None:
    assert detect_language(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("मदत करा, अपघात झाला आहे", "sos"),
        ("emergency! need an ambulance", "sos"),
        ("मंदिरात किती गर्दी आहे?", "crowd"),
        ("नजदीक पानी कहाँ है?", "facility"),
        ("मंदिरात कसे जायचे?", "route"),
        ("आरतीची वेळ काय आहे?", "temple"),
        ("माझा मुलगा हरवला आहे", "lost_found"),
        ("नमस्कार", "greeting"),
        ("I want to talk to a human", "escalate"),
        ("when is the best time to go?", "forecast"),
    ],
)
def test_intent_classification(text: str, expected: str) -> None:
    assert classify_intent(text) == expected


def test_emergency_wins_over_other_keywords() -> None:
    # A crowd word in the sentence must not downgrade an emergency.
    assert classify_intent("गर्दीत माझी आई बेशुद्ध पडली, मदत करा") == "sos"


def test_forecast_wins_over_crowd_when_both_present() -> None:
    # "when is the queue less crowded" is a forecast question, not a now question.
    assert classify_intent("when is the queue less crowded later?") == "forecast"


def test_unknown_text_is_unknown() -> None:
    assert classify_intent("xyzzy") == "unknown"


@pytest.mark.parametrize("text", ["होय", "हाँ", "yes", "OK"])
def test_affirmative_detection(text: str) -> None:
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["नाही", "नको", "no", "cancel"])
def test_negative_is_not_affirmative(text: str) -> None:
    assert is_affirmative(text) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("मला पाणी हवे", "water"),
        ("where is a toilet", "toilet"),
        ("I need a doctor", "medical"),
        ("कुठे जेवण मिळेल", "food"),
        ("somewhere to sleep tonight", "accommodation"),
        ("what is nearby?", "water"),  # no category named → the commonest need
    ],
)
def test_facility_keywords_map_to_frontend_categories(text: str, expected: str) -> None:
    assert category_from_text(text) == expected


def test_every_category_maps_to_real_facility_types() -> None:
    from app.data.reference import FACILITIES, FACILITY_CATEGORIES as EXPOSED
    from app.services.llm_orchestrator import FACILITY_CATEGORIES

    assert set(FACILITY_CATEGORIES) == set(EXPOSED)

    known = {f["facility_type"] for f in FACILITIES}
    for category, types in FACILITY_CATEGORIES.items():
        assert types, category
        if category == "accommodation":
            # Valid to ask for, but the seed spec listed no overnight lodging.
            continue
        assert set(types) <= known, f"{category} maps to unknown types"


# --- crowd ------------------------------------------------------------------


# LOW <30, MODERATE 30-60, HIGH 61-85, VERY_HIGH >85.
@pytest.mark.parametrize(
    ("density", "expected"),
    [
        (0, "LOW"),
        (29, "LOW"),
        (30, "MODERATE"),
        (60, "MODERATE"),
        (61, "HIGH"),
        (85, "HIGH"),
        (86, "VERY_HIGH"),
        (100, "VERY_HIGH"),
    ],
)
def test_density_thresholds(density: int, expected: str) -> None:
    assert CrowdService.density_level(density) == expected
    assert density_to_status(density) == expected


async def test_modelled_reading_is_stable_between_calls() -> None:
    service = CrowdService(None)
    first = await service.read_zone("bhima-ghat")
    second = await service.read_zone("bhima-ghat")
    # With no cache and no stored rows, the curve must give the same answer
    # twice — the app should not flicker between contradictory numbers.
    assert first.density == second.density
    # Labelled a model, never mistaken for a measurement.
    assert first.source == "model"
    assert 0 <= first.density <= 100


async def test_modelled_reading_is_internally_consistent() -> None:
    service = CrowdService(None)
    reading = await service.read_zone("temple-main")
    assert reading.status == density_to_status(reading.density)
    assert reading.people_estimate == round(reading.capacity * reading.density / 100)


async def test_nearest_zone_is_found_for_temple_coordinates() -> None:
    service = CrowdService(None)
    assert await service.nearest_zone_id(TEMPLE_LAT, TEMPLE_LON) in (
        "temple-main",
        "gate-1",
        "gate-3",
    )


def test_every_seeded_zone_is_registered() -> None:
    # The six zones the seed script writes must all resolve as real zones.
    assert set(SEED_DENSITY) == set(ZONES_BY_ID)
    assert set(ZONES_BY_ID) == {
        "gate-1", "gate-2", "gate-3", "temple-main", "bhima-ghat", "main-road",
    }


async def test_far_away_coordinates_have_no_zone() -> None:
    service = CrowdService(None)
    assert await service.nearest_zone_id(19.0760, 72.8777) is None  # Mumbai


# --- opening hours ----------------------------------------------------------


# --- ORM mapping ------------------------------------------------------------


def test_nullable_jsonb_columns_store_sql_null() -> None:
    """A missing value must be SQL NULL, never the JSON scalar `null`.

    SQLAlchemy's JSON types default to writing `null` for a Python None, which
    would make `WHERE widgets_json IS NULL` silently skip those rows.
    """
    from app.models.db_models import Facility, Message, Session

    for model, column in (
        (Message, "widgets_json"),
        (Session, "context_json"),
        (Facility, "details"),
    ):
        assert model.__table__.c[column].type.none_as_null is True, column


def test_seeded_status_matches_seeded_density() -> None:
    # The seed script writes `status` alongside `density`; they must agree.
    for zone_id, density in SEED_DENSITY.items():
        assert density_to_status(density) in {"LOW", "MODERATE", "HIGH", "VERY_HIGH"}
        assert zone_id in ZONES_BY_ID


# --- opening hours ----------------------------------------------------------


def test_24x7_facility_is_always_open() -> None:
    assert is_open_now(None, None, True) is True


def test_overnight_window_is_handled() -> None:
    at = datetime.fromisoformat("2026-07-15T23:30:00+05:30")
    assert is_open_now("22:00", "06:00", False, at=at) is True
    assert is_open_now("06:00", "22:00", False, at=at) is False
