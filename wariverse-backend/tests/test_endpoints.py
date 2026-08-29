from httpx import AsyncClient

from tests.conftest import TEMPLE_LAT, TEMPLE_LON


# --- crowd ------------------------------------------------------------------


# Crowd endpoints live in tests/test_crowd.py.


# --- facilities -------------------------------------------------------------


# Facilities, routes, temple and lost & found live in their own test files:
# test_facilities.py, test_routes.py, test_temple.py, test_lost_found.py.


# --- sos --------------------------------------------------------------------


async def test_sos_trigger_is_open_to_unauthenticated_pilgrims(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sos/trigger",
        json={
            "lat": TEMPLE_LAT,
            "lon": TEMPLE_LON,
            "emergency_type": "medical",
            "language": "mr",
        },
    )
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "ACTIVATED"
    assert body["dispatched_to"]
    assert body["session_id"]  # anonymous panic press still gets a session
    assert body["helpline_numbers"][0] == "112"
    assert body["nearest_facility"]["category"] in ("medical", "police")


# Auth lives in tests/test_auth.py and tests/test_auth_integration.py.
