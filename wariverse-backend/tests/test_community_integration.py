"""Seva end to end: publish → pin → AI search → withdraw.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.db_models import CommunityService
from app.utils import now_utc
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)

# Around Gate 2, so offerings land inside a normal nearby search.
LAT, LNG = 17.6795, 75.3295


@pytest.fixture
def spot() -> tuple[float, float]:
    """A coordinate unique to each test.

    Offerings persist across tests and runs, so publishing everything at one
    point makes each test see every other test's kitchens. ~200 m apart is
    enough to separate them while staying in the precinct.
    """
    return (
        LAT + random.uniform(-0.02, 0.02),
        LNG + random.uniform(-0.02, 0.02),
    )


def an_offering(at: tuple[float, float] | None = None, **overrides) -> dict:
    start = now_utc() - timedelta(minutes=5)
    lat, lng = at or (LAT, LNG)
    return {
        "provider_name": "Shri Ram Mandal",
        "category": "food",
        "title": f"Shri Ram Free Annachatra {uuid.uuid4().hex[:6]}",
        "address": "Near Gate 2, Pandharpur",
        "latitude": lat,
        "longitude": lng,
        "available_from": start.isoformat(),
        "available_until": (start + timedelta(days=3)).isoformat(),
        "contact_phone": "+919876543210",
        **overrides,
    }


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def publish(client: AsyncClient, at=None, **overrides) -> dict:
    response = await client.post(
        "/api/community/services", json=an_offering(at, **overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _clear_rate_limit(key: str) -> None:
    from app.redis_client import get_redis

    client = get_redis()
    if client is not None:
        await client.delete(f"wv:chat:rate:{key}")


# --- publishing -------------------------------------------------------------


async def test_publishing_persists_and_returns_a_manage_token(
    live_client: AsyncClient,
) -> None:
    body = await publish(live_client)

    assert body["id"].startswith("cs-")
    assert body["is_active"] is True
    assert body["is_open_now"] is True
    assert body["manage_token"]

    async with await _db() as db:
        row = (
            await db.execute(
                select(CommunityService).where(CommunityService.id == body["id"])
            )
        ).scalar_one()
    assert row.title == body["title"]
    assert row.category == "food"
    # Only the digest is stored — the token itself is never recoverable.
    assert row.owner_token_hash != body["manage_token"]
    assert len(row.owner_token_hash) == 64


async def test_publishing_is_open_to_anonymous_providers(
    live_client: AsyncClient,
) -> None:
    # A resident opening a langar should not need an account.
    body = await publish(live_client)
    async with await _db() as db:
        row = (
            await db.execute(
                select(CommunityService).where(CommunityService.id == body["id"])
            )
        ).scalar_one()
    assert row.user_id is None


# --- it pins to the map -----------------------------------------------------


async def test_offering_appears_in_the_public_list(live_client: AsyncClient) -> None:
    body = await publish(live_client)

    listed = (
        await live_client.get(
            "/api/community/services", params={"lat": LAT, "lng": LNG, "radius_m": 2000}
        )
    ).json()["services"]

    mine = next(s for s in listed if s["id"] == body["id"])
    assert mine["latitude"] == pytest.approx(LAT)
    assert mine["distance_m"] is not None
    assert mine["is_open_now"] is True


async def test_listing_can_be_filtered_by_category(live_client: AsyncClient) -> None:
    food = await publish(live_client, category="food")
    await publish(live_client, category="accommodation")

    listed = (
        await live_client.get("/api/community/services", params={"category": "food"})
    ).json()["services"]
    ids = [s["id"] for s in listed]
    assert food["id"] in ids
    assert all(s["category"] == "food" for s in listed)


async def test_expired_offerings_do_not_pin(live_client: AsyncClient) -> None:
    """A pin for a kitchen that closed yesterday sends someone on a wasted walk."""
    past = now_utc() - timedelta(days=5)
    body = await publish(
        live_client,
        available_from=past.isoformat(),
        available_until=(past + timedelta(days=1)).isoformat(),
    )

    listed = (await live_client.get("/api/community/services")).json()["services"]
    assert body["id"] not in [s["id"] for s in listed]


async def test_future_offerings_do_not_pin_yet(live_client: AsyncClient) -> None:
    future = now_utc() + timedelta(days=2)
    body = await publish(
        live_client,
        available_from=future.isoformat(),
        available_until=(future + timedelta(days=1)).isoformat(),
    )
    listed = (await live_client.get("/api/community/services")).json()["services"]
    assert body["id"] not in [s["id"] for s in listed]


# --- the AI finds it --------------------------------------------------------


async def test_seva_appears_in_the_facilities_endpoint(
    live_client: AsyncClient, spot
) -> None:
    # Its own coordinate: offerings persist across runs, and a shared point
    # eventually pushes the new one past the result limit.
    body = await publish(live_client, at=spot)

    found = (
        await live_client.get(
            "/api/facilities/nearby",
            params={"lat": spot[0], "lng": spot[1], "category": "food",
                    "radius_m": 2000, "language": "en"},
        )
    ).json()["facilities"]

    seva = next(f for f in found if f["id"] == body["id"])
    assert seva["is_seva"] is True
    assert seva["provider_name"] == "Shri Ram Mandal"
    # A pilgrim must be able to tell a donated meal from an official kitchen.
    assert "Free seva" in seva["availability"]
    assert seva["contact"] == "+919876543210"


async def test_official_facilities_are_not_marked_as_seva(
    live_client: AsyncClient,
) -> None:
    found = (
        await live_client.get(
            "/api/facilities/nearby",
            params={"lat": LAT, "lng": LNG, "category": "toilet", "radius_m": 3000},
        )
    ).json()["facilities"]
    assert found
    assert all(f["is_seva"] is False for f in found)


@pytest.mark.parametrize(
    "question",
    ["Free food nearby?", "Annachatra near me", "where is the langar?"],
)
async def test_asking_for_free_food_surfaces_the_offering(
    live_client: AsyncClient, spot, question: str
) -> None:
    body = await publish(live_client, at=spot)
    session = f"seva-{random.randint(0, 10**9)}"
    await _clear_rate_limit(session)

    answer = (
        await live_client.post(
            "/api/conversation/message",
            json={
                "session_id": session,
                "language": "en",
                "message": question,
                "latitude": spot[0],
                "longitude": spot[1],
            },
        )
    ).json()

    widgets = [w for w in answer["widgets"] if w["type"] == "nearby_facility"]
    assert widgets, f"no facility widget for: {question}"
    assert body["id"] in [w["data"]["id"] for w in widgets]

    card = next(w["data"] for w in widgets if w["data"]["id"] == body["id"])
    assert card["is_seva"] is True
    assert card["provider_name"] == "Shri Ram Mandal"
    # The spoken reply must not double the unit — this read "(0 m m away)".
    assert " m m " not in answer["response_text"]
    assert " km km" not in answer["response_text"]


async def test_asking_for_free_accommodation_surfaces_a_room(
    live_client: AsyncClient, spot
) -> None:
    body = await publish(
        live_client, at=spot, category="accommodation", title="Free rooms for pilgrims"
    )
    session = f"seva-{random.randint(0, 10**9)}"
    await _clear_rate_limit(session)

    answer = (
        await live_client.post(
            "/api/conversation/message",
            json={
                "session_id": session,
                "language": "en",
                "message": "Free accommodation available?",
                "latitude": spot[0],
                "longitude": spot[1],
            },
        )
    ).json()

    ids = [w["data"]["id"] for w in answer["widgets"] if w["type"] == "nearby_facility"]
    assert body["id"] in ids


async def test_seva_is_ordered_by_distance_alongside_official_places(
    live_client: AsyncClient,
) -> None:
    # Published at the reference point so it sorts among the real facilities.
    await publish(live_client, at=(LAT, LNG))
    found = (
        await live_client.get(
            "/api/facilities/nearby",
            params={"lat": LAT, "lng": LNG, "radius_m": 4000, "limit": 50},
        )
    ).json()["facilities"]

    kinds = {f["is_seva"] for f in found}
    assert kinds == {True, False}, "expected both official and seva results"
    # One merged list, nearest first — not seva bolted on at the end.
    metres = [_metres(f["distance"]) for f in found]
    assert metres == sorted(metres)


def _metres(distance: str) -> float:
    """Parse the leading magnitude from a rendered distance.

    The string carries extra detail now — `0.1 km (North-East • 1 min walk)` —
    so only the first two tokens are the measurement.
    """
    parts = distance.split()
    return float(parts[0]) * (1000 if parts[1].startswith("km") else 1)


# --- withdrawing ------------------------------------------------------------


async def test_the_provider_can_withdraw_with_their_token(
    live_client: AsyncClient,
) -> None:
    body = await publish(live_client)

    removed = await live_client.delete(
        f"/api/community/services/{body['id']}",
        headers={"X-Manage-Token": body["manage_token"]},
    )
    assert removed.status_code == 200
    assert removed.json()["is_active"] is False

    # Gone from the map...
    listed = (await live_client.get("/api/community/services")).json()["services"]
    assert body["id"] not in [s["id"] for s in listed]

    # ...and from search, instantly.
    found = (
        await live_client.get(
            "/api/facilities/nearby",
            params={"lat": LAT, "lng": LNG, "category": "food", "radius_m": 2000},
        )
    ).json()["facilities"]
    assert body["id"] not in [f["id"] for f in found]


async def test_a_stranger_cannot_withdraw_someone_elses_offering(
    live_client: AsyncClient,
) -> None:
    """The list endpoint hands out ids; deletion must not follow from knowing one."""
    body = await publish(live_client)

    refused = await live_client.delete(f"/api/community/services/{body['id']}")
    assert refused.status_code == 403

    wrong_token = await live_client.delete(
        f"/api/community/services/{body['id']}",
        headers={"X-Manage-Token": "not-the-right-token"},
    )
    assert wrong_token.status_code == 403

    # Still serving food.
    listed = (await live_client.get("/api/community/services")).json()["services"]
    assert body["id"] in [s["id"] for s in listed]


async def test_an_admin_can_withdraw_anything(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "integration-admin-key", raising=False)
    body = await publish(live_client)

    removed = await live_client.delete(
        f"/api/community/services/{body['id']}",
        headers={"X-API-Key": "integration-admin-key"},
    )
    assert removed.status_code == 200


async def test_withdrawing_twice_is_a_404(live_client: AsyncClient) -> None:
    body = await publish(live_client)
    headers = {"X-Manage-Token": body["manage_token"]}

    assert (
        await live_client.delete(f"/api/community/services/{body['id']}", headers=headers)
    ).status_code == 200
    assert (
        await live_client.delete(f"/api/community/services/{body['id']}", headers=headers)
    ).status_code == 404


async def test_withdrawal_is_a_soft_delete(live_client: AsyncClient) -> None:
    body = await publish(live_client)
    await live_client.delete(
        f"/api/community/services/{body['id']}",
        headers={"X-Manage-Token": body["manage_token"]},
    )

    async with await _db() as db:
        row = (
            await db.execute(
                select(CommunityService).where(CommunityService.id == body["id"])
            )
        ).scalar_one()
    # Kept, so a mistaken withdrawal can be undone and the control room retains
    # a record of what was offered where.
    assert row.is_active is False


# --- the provider's own list ------------------------------------------------


async def test_a_provider_can_find_their_own_listings(
    live_client: AsyncClient,
) -> None:
    body = await publish(live_client)

    mine = (
        await live_client.get(
            "/api/community/services",
            params={"mine": "true"},
            headers={"X-Manage-Token": body["manage_token"]},
        )
    ).json()["services"]

    assert [s["id"] for s in mine] == [body["id"]]


async def test_mine_returns_nothing_without_a_token(live_client: AsyncClient) -> None:
    await publish(live_client)
    mine = (
        await live_client.get("/api/community/services", params={"mine": "true"})
    ).json()["services"]
    assert mine == []


async def test_mine_includes_withdrawn_listings(live_client: AsyncClient) -> None:
    # The Settings page should show what you took down, not silently lose it.
    body = await publish(live_client)
    headers = {"X-Manage-Token": body["manage_token"]}
    await live_client.delete(f"/api/community/services/{body['id']}", headers=headers)

    mine = (
        await live_client.get(
            "/api/community/services", params={"mine": "true"}, headers=headers
        )
    ).json()["services"]
    assert [s["id"] for s in mine] == [body["id"]]
    assert mine[0]["is_active"] is False
