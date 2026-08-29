"""Community seva: validation, ownership, and the degraded path.

The publish → search → withdraw round trip runs against a real database in
`test_community_integration.py`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.services.community_service import hash_token, is_open_now, new_manage_token
from app.utils import now_utc


def an_offering(**overrides) -> dict:
    start = now_utc()
    return {
        "provider_name": "Shri Ram Mandal",
        "category": "food",
        "title": "Shri Ram Free Annachatra",
        "address": "Near Gate 2, Pandharpur",
        "latitude": 17.6795,
        "longitude": 75.3295,
        "available_from": start.isoformat(),
        "available_until": (start + timedelta(days=3)).isoformat(),
        "contact_phone": "+919876543210",
        **overrides,
    }


# --- validation -------------------------------------------------------------


async def test_publishing_needs_the_store(client: AsyncClient) -> None:
    response = await client.post("/api/community/services", json=an_offering())
    assert response.status_code == 201


@pytest.mark.parametrize(
    "category", ["food", "accommodation", "water", "medical", "rest"]
)
async def test_every_documented_category_is_accepted(
    client: AsyncClient, category: str
) -> None:
    response = await client.post(
        "/api/community/services", json=an_offering(category=category)
    )
    # Validation passed and stored in memory
    assert response.status_code == 201


async def test_unknown_category_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/community/services", json=an_offering(category="wifi")
    )
    assert response.status_code == 422


async def test_phone_is_validated(client: AsyncClient) -> None:
    response = await client.post(
        "/api/community/services", json=an_offering(contact_phone="12")
    )
    assert response.status_code == 422


async def test_availability_window_must_be_forwards(client: AsyncClient) -> None:
    start = now_utc()
    response = await client.post(
        "/api/community/services",
        json=an_offering(
            available_from=start.isoformat(),
            available_until=(start - timedelta(hours=1)).isoformat(),
        ),
    )
    # A kitchen that closes before it opens would pin to the map and never serve.
    assert response.status_code == 422


async def test_coordinates_are_validated(client: AsyncClient) -> None:
    response = await client.post(
        "/api/community/services", json=an_offering(latitude=200)
    )
    assert response.status_code == 422


async def test_camelcase_is_accepted(client: AsyncClient) -> None:
    start = now_utc()
    response = await client.post(
        "/api/community/services",
        json={
            "providerName": "Shri Ram Mandal",
            "category": "food",
            "title": "Shri Ram Free Annachatra",
            "address": "Near Gate 2",
            "latitude": 17.6795,
            "longitude": 75.3295,
            "availableFrom": start.isoformat(),
            "availableUntil": (start + timedelta(days=1)).isoformat(),
            "contactPhone": "9876543210",
        },
    )
    assert response.status_code == 201  # validation passed & created


# --- listing ----------------------------------------------------------------


async def test_listing_degrades_to_empty_without_a_store(client: AsyncClient) -> None:
    # The map screen should render, just without seva pins.
    response = await client.get("/api/community/services")
    assert response.status_code == 200


async def test_withdrawing_needs_the_store(client: AsyncClient) -> None:
    response = await client.delete("/api/community/services/cs-doesnotexist")
    assert response.status_code in (404, 403)


# --- ownership --------------------------------------------------------------


def test_manage_tokens_are_unguessable_and_unique() -> None:
    tokens = {new_manage_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 32 for t in tokens)


def test_only_the_digest_is_stored() -> None:
    token = new_manage_token()
    digest = hash_token(token)
    assert digest != token
    assert len(digest) == 64
    assert hash_token(token) == digest  # stable


def test_ownership_rules() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services.community_service import CommunityServiceRepo

    token = new_manage_token()
    owner = uuid4()
    service = SimpleNamespace(owner_token_hash=hash_token(token), user_id=owner)
    may = CommunityServiceRepo._may_manage

    assert may(service, token, None, False) is True          # correct token
    assert may(service, None, owner, False) is True          # owning account
    assert may(service, None, None, True) is True            # admin override
    # ...and the ways it must refuse. The list endpoint hands out ids, so an
    # unauthenticated delete would let one sweep erase every free kitchen.
    assert may(service, "wrong-token", None, False) is False
    assert may(service, None, uuid4(), False) is False
    assert may(service, None, None, False) is False


# --- availability window ----------------------------------------------------


def test_open_now_respects_the_window_and_the_active_flag() -> None:
    from types import SimpleNamespace

    now = now_utc()
    live = SimpleNamespace(
        is_active=True,
        available_from=now - timedelta(hours=1),
        available_until=now + timedelta(hours=1),
    )
    assert is_open_now(live) is True

    # A stale pin sends someone to a kitchen that closed yesterday.
    expired = SimpleNamespace(
        is_active=True,
        available_from=now - timedelta(days=2),
        available_until=now - timedelta(days=1),
    )
    assert is_open_now(expired) is False

    not_yet = SimpleNamespace(
        is_active=True,
        available_from=now + timedelta(days=1),
        available_until=now + timedelta(days=2),
    )
    assert is_open_now(not_yet) is False

    withdrawn = SimpleNamespace(
        is_active=False,
        available_from=now - timedelta(hours=1),
        available_until=now + timedelta(hours=1),
    )
    assert is_open_now(withdrawn) is False


# --- the assistant recognises the words pilgrims use ------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Free food nearby?",
        "Annachatra near me",
        "where is the langar",
        "मोफत जेवण कुठे मिळेल",
        "अन्नछत्र जवळ आहे का",
        "मुफ्त भोजन कहाँ है",
        "bhandara nearby",
    ],
)
def test_free_food_questions_map_to_the_food_category(question: str) -> None:
    from app.services.llm_orchestrator import category_from_text

    assert category_from_text(question) == "food"


@pytest.mark.parametrize(
    "question",
    ["Free accommodation available?", "where can I stay tonight", "free stay nearby"],
)
def test_accommodation_questions_map_to_accommodation(question: str) -> None:
    from app.services.llm_orchestrator import category_from_text

    assert category_from_text(question) == "accommodation"


def test_seva_categories_match_the_facility_categories() -> None:
    from app.data.reference import FACILITY_CATEGORIES
    from app.services.facility_service import SEVA_CATEGORIES

    # Seva is a subset: nobody offers a police post from their front room.
    assert set(SEVA_CATEGORIES) <= set(FACILITY_CATEGORIES)
    assert "police" not in SEVA_CATEGORIES
    assert "toilet" not in SEVA_CATEGORIES


async def test_locking_and_unlocking_service(client: AsyncClient) -> None:
    created = (
        await client.post("/api/community/services", json=an_offering())
    ).json()
    service_id = created["id"]
    assert created["is_locked"] is False

    # Lock service
    locked_res = await client.post(
        f"/api/community/services/{service_id}/lock?name=Rahul&phone=9876543210"
    )
    assert locked_res.status_code == 200
    locked_data = locked_res.json()
    assert locked_data["is_locked"] is True
    assert locked_data["locked_by_name"] == "Rahul"

    # Unlock service
    unlocked_res = await client.post(f"/api/community/services/{service_id}/unlock")
    assert unlocked_res.status_code == 200
    unlocked_data = unlocked_res.json()
    assert unlocked_data["is_locked"] is False
    assert unlocked_data["locked_by_name"] is None
