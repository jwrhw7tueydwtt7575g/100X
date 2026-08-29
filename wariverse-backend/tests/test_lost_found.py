"""Lost & found: request validation and the degraded path.

Filing needs Postgres (the reference sequence lives there), so the round trip
is covered in `test_lost_found_integration.py`.
"""

from __future__ import annotations

from httpx import AsyncClient

VALID = {
    "incident_type": "PERSON",
    "description": "65 year old woman, white saree, lost near Gate 2",
    "reporter_phone": "+919876543210",
    "last_seen_location": "Gate 2 area",
    "session_id": "wariverse-session",
}


async def test_filing_needs_the_report_store(client: AsyncClient) -> None:
    # Accepting a report we cannot store would be a lie to someone whose
    # relative is missing.
    response = await client.post("/api/lost-found", json=VALID)
    assert response.status_code == 503
    assert "1800-233-1000" in response.json()["error"]["message"]


async def test_lookup_needs_the_report_store(client: AsyncClient) -> None:
    response = await client.get("/api/lost-found/WF-2026-00124")
    assert response.status_code == 503


async def test_phone_is_validated(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lost-found", json={**VALID, "reporter_phone": "12"}
    )
    assert response.status_code == 422


async def test_incident_type_is_constrained(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lost-found", json={**VALID, "incident_type": "vehicle"}
    )
    assert response.status_code == 422


async def test_description_cannot_be_empty(client: AsyncClient) -> None:
    response = await client.post("/api/lost-found", json={**VALID, "description": "x"})
    assert response.status_code == 422


async def test_optional_fields_may_be_omitted(client: AsyncClient) -> None:
    minimal = {
        "incident_type": "ITEM",
        "description": "Cloth bag with medicines, left near the water point",
        "reporter_phone": "9876543210",
    }
    # Still 503 without a database, but must pass validation to get there.
    response = await client.post("/api/lost-found", json=minimal)
    assert response.status_code == 503


async def test_camelcase_fields_are_accepted(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lost-found",
        json={
            "incidentType": "PERSON",
            "description": "65 year old woman, white saree, lost near Gate 2",
            "reporterPhone": "+919876543210",
            "lastSeenLocation": "Gate 2 area",
            "sessionId": "wariverse-session",
        },
    )
    assert response.status_code == 503  # validation passed, store is down


def test_reference_format() -> None:
    import re

    from app.utils import now_ist

    pattern = re.compile(r"^WF-\d{4}-\d{5}$")
    assert pattern.match(f"WF-{now_ist():%Y}-{124:05d}")
    assert f"WF-{2026}-{124:05d}" == "WF-2026-00124"


def test_status_labels_cover_every_stored_status() -> None:
    from app.data.i18n import PHRASES
    from app.routers.lost_found import STATUS_LABELS

    # Must match the CHECK constraint on lost_found_reports.status.
    assert set(STATUS_LABELS) == {
        "OPEN", "IN_PROGRESS", "MATCHED", "RESOLVED", "CLOSED"
    }
    for key in STATUS_LABELS.values():
        assert key in PHRASES, key
        assert {"en", "mr", "hi"} <= set(PHRASES[key])


def test_open_reports_read_as_searching() -> None:
    from app.data.i18n import t
    from app.routers.lost_found import STATUS_LABELS

    assert t(STATUS_LABELS["OPEN"], "en") == "Searching"
    assert t(STATUS_LABELS["RESOLVED"], "en") == "Reunited"
