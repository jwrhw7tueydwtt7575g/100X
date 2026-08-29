"""Lost & found round trip, and temple info persistence, against real services.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.db_models import LostFoundReport, TempleInfo
from app.routers.lost_found import LOST_FOUND_CHANNEL
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)

API_KEY = "integration-admin-key"

REPORT = {
    "incident_type": "PERSON",
    "description": "65 year old woman, white saree, lost near Gate 2",
    "reporter_phone": "+919876543210",
    "last_seen_location": "Gate 2 area",
    "session_id": "wariverse-session",
}


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


# --- filing -----------------------------------------------------------------


async def test_filing_returns_the_documented_response(live_client: AsyncClient) -> None:
    response = await live_client.post(
        "/api/lost-found", json=REPORT, params={"language": "en"}
    )
    assert response.status_code == 201

    body = response.json()
    assert body["reference_id"].startswith("WF-")
    assert body["status"] == "Searching"
    assert body["next_action"] == (
        "Stay near the last known location and keep your phone reachable."
    )
    assert body["message"] == "Your report has been shared with volunteer team."


async def test_reference_ids_are_sequential_and_five_digits(
    live_client: AsyncClient,
) -> None:
    import re

    refs = []
    for _ in range(3):
        body = (await live_client.post("/api/lost-found", json=REPORT)).json()
        refs.append(body["reference_id"])

    for ref in refs:
        assert re.fullmatch(r"WF-\d{4}-\d{5}", ref), ref

    numbers = [int(r.split("-")[2]) for r in refs]
    assert numbers == sorted(numbers)
    assert numbers[1] == numbers[0] + 1


async def test_report_is_persisted_with_its_details(live_client: AsyncClient) -> None:
    body = (await live_client.post("/api/lost-found", json=REPORT)).json()

    async with await _db() as db:
        report = (
            await db.execute(
                select(LostFoundReport).where(
                    LostFoundReport.reference_id == body["reference_id"]
                )
            )
        ).scalar_one()

    assert report.incident_type == "PERSON"
    assert report.last_seen_location == "Gate 2 area"
    assert report.status == "OPEN"
    # Linked to the chat it came from, so a volunteer can read the context.
    assert report.session_id is not None


async def test_lookup_returns_the_current_status(live_client: AsyncClient) -> None:
    filed = (
        await live_client.post("/api/lost-found", json=REPORT, params={"language": "en"})
    ).json()

    found = await live_client.get(
        f"/api/lost-found/{filed['reference_id']}", params={"language": "en"}
    )
    assert found.status_code == 200

    body = found.json()
    assert body["reference_id"] == filed["reference_id"]
    assert body["status"] == "Searching"
    assert body["last_seen_location"] == "Gate 2 area"


async def test_lookup_reflects_a_status_change(live_client: AsyncClient) -> None:
    filed = (await live_client.post("/api/lost-found", json=REPORT)).json()

    async with await _db() as db:
        report = (
            await db.execute(
                select(LostFoundReport).where(
                    LostFoundReport.reference_id == filed["reference_id"]
                )
            )
        ).scalar_one()
        report.status = "RESOLVED"
        await db.commit()

    body = (
        await live_client.get(
            f"/api/lost-found/{filed['reference_id']}", params={"language": "en"}
        )
    ).json()
    assert body["status"] == "Reunited"


async def test_unknown_reference_returns_404(live_client: AsyncClient) -> None:
    response = await live_client.get("/api/lost-found/WF-1999-99999")
    assert response.status_code == 404


async def test_status_is_localized(live_client: AsyncClient) -> None:
    filed = (await live_client.post("/api/lost-found", json=REPORT)).json()
    body = (
        await live_client.get(
            f"/api/lost-found/{filed['reference_id']}", params={"language": "mr"}
        )
    ).json()
    assert body["status"] == "शोध सुरू"


# --- dashboard notification -------------------------------------------------


async def test_new_report_is_published_for_the_dashboard(
    live_client: AsyncClient,
) -> None:
    from app.redis_client import get_redis

    pubsub = get_redis().pubsub()
    await pubsub.subscribe(LOST_FOUND_CHANNEL)
    try:
        filed = (await live_client.post("/api/lost-found", json=REPORT)).json()

        message = None
        for _ in range(40):  # up to ~2s
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.05
            )
            if message:
                break
            await asyncio.sleep(0.05)

        assert message is not None, "the dashboard never received the report"
        payload = json.loads(message["data"])
        assert payload["reference_id"] == filed["reference_id"]
        assert payload["last_seen_location"] == "Gate 2 area"
    finally:
        await pubsub.unsubscribe(LOST_FOUND_CHANNEL)
        await pubsub.aclose()


async def test_report_survives_a_failed_publish(live_client: AsyncClient) -> None:
    """The report is committed before publishing, so a dead dashboard cannot
    cost a family their reference number."""
    import app.redis_client as redis_client

    healthy = redis_client._healthy
    redis_client._healthy = False
    try:
        response = await live_client.post("/api/lost-found", json=REPORT)
        assert response.status_code == 201
        assert response.json()["reference_id"]
    finally:
        redis_client._healthy = healthy


# --- temple info ------------------------------------------------------------


async def test_temple_info_is_seeded_and_served(live_client: AsyncClient) -> None:
    from app.services.temple_service import TempleService

    async with await _db() as db:
        await TempleService(db).seed_defaults()

    body = (await live_client.get("/api/temple/info", params={"language": "en"})).json()
    assert body["title"] == "Vitthal Temple — Pandharpur"


async def test_admin_update_persists_and_busts_the_cache(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)

    # Warm the cache first, so this proves the cache is actually dropped.
    await live_client.get("/api/temple/info", params={"language": "en"})

    updated = await live_client.put(
        "/api/admin/temple/info",
        json={"timings": "5:00 AM – 11:30 PM", "language": "en"},
        headers={"X-API-Key": API_KEY},
    )
    assert updated.status_code == 200
    assert updated.json()["timings"] == "5:00 AM – 11:30 PM"

    # A correction during the Wari must be visible now, not in an hour.
    fresh = (await live_client.get("/api/temple/info", params={"language": "en"})).json()
    assert fresh["timings"] == "5:00 AM – 11:30 PM"

    async with await _db() as db:
        row = (
            await db.execute(select(TempleInfo).where(TempleInfo.language == "en"))
        ).scalar_one()
    assert row.timings == "5:00 AM – 11:30 PM"


async def test_admin_update_leaves_other_fields_alone(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)

    before = (await live_client.get("/api/temple/info", params={"language": "en"})).json()
    await live_client.put(
        "/api/admin/temple/info",
        json={"description": "Updated guidance for Ekadashi.", "language": "en"},
        headers={"X-API-Key": API_KEY},
    )
    after = (await live_client.get("/api/temple/info", params={"language": "en"})).json()

    assert after["description"] == "Updated guidance for Ekadashi."
    assert after["rituals"] == before["rituals"]
    assert after["title"] == before["title"]


async def test_admin_can_replace_the_ritual_list(
    live_client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", API_KEY, raising=False)
    response = await live_client.put(
        "/api/admin/temple/info",
        json={"rituals": ["Morning aarti · 7:00 AM"], "language": "mr"},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    assert response.json()["rituals"] == ["Morning aarti · 7:00 AM"]

    # ...and only for that language.
    english = (await live_client.get("/api/temple/info", params={"language": "en"})).json()
    assert len(english["rituals"]) > 1
