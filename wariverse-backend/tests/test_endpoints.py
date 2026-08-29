from httpx import AsyncClient

from tests.conftest import TEMPLE_LAT, TEMPLE_LON


# --- crowd ------------------------------------------------------------------


async def test_crowd_zone_returns_estimate_when_no_snapshots(client: AsyncClient) -> None:
    response = await client.get("/api/crowd/vitthal_temple", params={"language": "mr"})
    assert response.status_code == 200

    body = response.json()
    assert body["zone_id"] == "vitthal_temple"
    assert body["density_level"] in ("low", "moderate", "high", "critical")
    # No database and no sensor feed: the reading must be labelled as estimated.
    assert body["source"] == "estimated"
    assert 0 <= body["occupancy_ratio"] <= 1.3
    assert body["advice"]


async def test_crowd_unknown_zone_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/crowd/does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


# --- facilities -------------------------------------------------------------


async def test_facilities_nearby_sorted_by_distance(client: AsyncClient) -> None:
    response = await client.get(
        "/api/facilities/nearby",
        params={"lat": TEMPLE_LAT, "lon": TEMPLE_LON, "radius_m": 2000, "language": "en"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["count"] > 0
    distances = [f["distance_m"] for f in body["facilities"]]
    assert distances == sorted(distances)
    assert all(d <= 2000 for d in distances)


async def test_facilities_can_be_filtered_by_type(client: AsyncClient) -> None:
    response = await client.get(
        "/api/facilities/nearby",
        params=[
            ("lat", TEMPLE_LAT),
            ("lon", TEMPLE_LON),
            ("radius_m", 5000),
            ("facility_type", "medical"),
            ("facility_type", "police"),
        ],
    )
    assert response.status_code == 200

    types = {f["facility_type"] for f in response.json()["facilities"]}
    assert types <= {"medical", "police"}
    assert types


async def test_facilities_rejects_unknown_type(client: AsyncClient) -> None:
    response = await client.get(
        "/api/facilities/nearby",
        params={"lat": TEMPLE_LAT, "lon": TEMPLE_LON, "facility_type": "helipad"},
    )
    assert response.status_code == 422


# --- routes -----------------------------------------------------------------


async def test_route_guidance_from_wakhari_to_temple(client: AsyncClient) -> None:
    response = await client.get(
        "/api/routes/guidance",
        params={"lat": 17.6903, "lon": 75.2787, "destination": "vitthal_temple",
                "language": "mr"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["steps"], "expected at least one step"
    assert body["distance_km"] > 0
    assert body["eta_minutes"] > 0
    # Steps must be ordered and monotonically increasing in cumulative distance.
    cumulative = [s["cumulative_distance_m"] for s in body["steps"]]
    assert cumulative == sorted(cumulative)


async def test_route_guidance_unknown_destination_returns_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/routes/guidance",
        params={"lat": TEMPLE_LAT, "lon": TEMPLE_LON, "destination": "atlantis"},
    )
    assert response.status_code == 404


# --- temple -----------------------------------------------------------------


async def test_temple_info_is_localized(client: AsyncClient) -> None:
    marathi = await client.get("/api/temple/info", params={"language": "mr"})
    english = await client.get("/api/temple/info", params={"language": "en"})
    assert marathi.status_code == english.status_code == 200

    mr_body, en_body = marathi.json(), english.json()
    assert mr_body["name"] != en_body["name"]
    assert mr_body["language"] == "mr"
    assert len(en_body["aarti_schedule"]) == len(mr_body["aarti_schedule"])
    assert en_body["darshan_types"][0]["starts_at"] == "04:00"
    assert en_body["queue_status"] in ("low", "moderate", "high", "critical")


async def test_unsupported_language_falls_back_to_default(client: AsyncClient) -> None:
    response = await client.get("/api/temple/info", params={"language": "fr"})
    assert response.status_code == 200
    assert response.json()["language"] == "mr"


# --- lost & found -----------------------------------------------------------


async def test_lost_found_returns_503_without_database(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lost-found",
        json={
            "report_type": "person",
            "description": "Seven year old boy in a yellow shirt, near Namdev Payri",
            "reporter_name": "Sunita Pawar",
            "contact_phone": "9876543210",
        },
    )
    # Losing the report silently would be worse than a retryable error.
    assert response.status_code == 503


async def test_lost_found_validates_phone(client: AsyncClient) -> None:
    response = await client.post(
        "/api/lost-found",
        json={
            "report_type": "person",
            "description": "Missing elderly man with a walking stick",
            "reporter_name": "Ramesh",
            "contact_phone": "12",
        },
    )
    assert response.status_code == 422


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
    assert body["status"] == "dispatched"
    assert body["dispatched_to"]
    assert body["helpline_numbers"][0] == "112"
    assert body["nearest_facility"]["facility_type"] in ("medical", "police")


# --- auth -------------------------------------------------------------------


async def test_otp_send_echoes_code_outside_production(client: AsyncClient) -> None:
    response = await client.post("/api/auth/otp/send", json={"phone": "9876543210"})
    assert response.status_code == 200

    body = response.json()
    assert body["phone"] == "+919876543210"  # bare 10-digit numbers get +91
    assert body["debug_otp"] and len(body["debug_otp"]) == 6
    assert body["expires_in_seconds"] == 300


async def test_otp_verify_rejects_wrong_code(client: AsyncClient) -> None:
    await client.post("/api/auth/otp/send", json={"phone": "9812345678"})
    response = await client.post(
        "/api/auth/otp/verify", json={"phone": "9812345678", "otp": "000000"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "incorrect code"


async def test_otp_verify_needs_user_store(client: AsyncClient) -> None:
    sent = await client.post("/api/auth/otp/send", json={"phone": "9811111111"})
    otp = sent.json()["debug_otp"]

    response = await client.post(
        "/api/auth/otp/verify", json={"phone": "9811111111", "otp": otp}
    )
    # The code is accepted; issuing a token needs Postgres, which is down here.
    assert response.status_code == 503


async def test_otp_verify_without_active_code(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/otp/verify", json={"phone": "9800000000", "otp": "123456"}
    )
    assert response.status_code == 400
