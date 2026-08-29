"""Unit tests for the domain logic behind the endpoints."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services.crowd_service import CrowdService
from app.services.geo import bounding_box, haversine_m, walk_minutes
from app.services.llm_orchestrator import (
    classify_intent,
    detect_language,
    facility_types_for,
    is_affirmative,
)
from app.utils import is_open_now
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
    ],
)
def test_intent_classification(text: str, expected: str) -> None:
    intent, confidence = classify_intent(text)
    assert intent == expected
    assert 0 < confidence <= 1


def test_emergency_wins_over_other_keywords() -> None:
    # A crowd word in the sentence must not downgrade an emergency.
    intent, confidence = classify_intent("गर्दीत माझी आई बेशुद्ध पडली, मदत करा")
    assert intent == "sos"
    assert confidence > 0.6


def test_unknown_text_is_low_confidence() -> None:
    intent, confidence = classify_intent("xyzzy")
    assert intent == "unknown"
    assert confidence < 0.5


@pytest.mark.parametrize("text", ["होय", "हाँ", "yes", "OK"])
def test_affirmative_detection(text: str) -> None:
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", ["नाही", "नको", "no", "cancel"])
def test_negative_is_not_affirmative(text: str) -> None:
    assert is_affirmative(text) is False


def test_facility_keywords_map_to_types() -> None:
    assert facility_types_for("मला पाणी हवे") == ["water"]
    assert set(facility_types_for("toilet and food")) == {"toilet", "food"}
    # No specific type mentioned → the essentials.
    assert "medical" in facility_types_for("what is nearby?")


# --- crowd ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.1, "low"), (0.5, "moderate"), (0.7, "high"), (0.95, "critical")],
)
def test_density_thresholds(ratio: float, expected: str) -> None:
    assert CrowdService.density_level(ratio) == expected


async def test_zone_estimate_is_stable_within_the_hour() -> None:
    service = CrowdService(None)
    first = await service.read_zone("chandrabhaga_ghat")
    second = await service.read_zone("chandrabhaga_ghat")
    # Without a cache, two reads in the same hour must still agree.
    assert first.people_estimate == second.people_estimate
    assert first.source == "estimated"


async def test_nearest_zone_is_found_for_temple_coordinates() -> None:
    service = CrowdService(None)
    assert await service.nearest_zone_id(TEMPLE_LAT, TEMPLE_LON) in (
        "vitthal_temple",
        "namdev_payri",
        "darshan_queue",
    )


async def test_far_away_coordinates_have_no_zone() -> None:
    service = CrowdService(None)
    assert await service.nearest_zone_id(19.0760, 72.8777) is None  # Mumbai


# --- opening hours ----------------------------------------------------------


def test_24x7_facility_is_always_open() -> None:
    assert is_open_now(None, None, True) is True


def test_overnight_window_is_handled() -> None:
    at = datetime.fromisoformat("2026-07-15T23:30:00+05:30")
    assert is_open_now("22:00", "06:00", False, at=at) is True
    assert is_open_now("06:00", "22:00", False, at=at) is False
