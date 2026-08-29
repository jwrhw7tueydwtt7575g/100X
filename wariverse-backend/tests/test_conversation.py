"""The endpoints the frontend calls in place of `mockConversationApi`.

The LLM is disabled here, so these exercise the keyword fallback — which must
produce the same response shape and the same widget shapes as the model path,
because that is what the app renders when OpenAI is unreachable.
"""

from httpx import AsyncClient

from tests.conftest import TEMPLE_LAT, TEMPLE_LON

SESSION = "wariverse-session"


def widget_types(body: dict) -> list[str]:
    return [w["type"] for w in body["widgets"]]


async def send(client: AsyncClient, **body) -> dict:
    body.setdefault("session_id", SESSION)
    response = await client.post("/api/conversation/message", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- response contract ------------------------------------------------------


async def test_matches_the_frontend_conversation_response(client: AsyncClient) -> None:
    body = await send(client, message="How crowded is Gate 3?", language="en")

    assert set(body) == {
        "session_id",
        "message_id",
        "language",
        "response_text",
        "widgets",
    }
    # The client's session id comes back verbatim, not translated to a UUID.
    assert body["session_id"] == SESSION
    assert body["message_id"].startswith("assistant-")
    assert body["language"] == "en"
    assert isinstance(body["response_text"], str) and body["response_text"]


async def test_flat_latitude_longitude_are_accepted(client: AsyncClient) -> None:
    body = await send(
        client,
        message="where is water?",
        language="en",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    assert widget_types(body)[0] == "nearby_facility"


async def test_camelcase_request_fields_are_accepted(client: AsyncClient) -> None:
    # The frontend is TypeScript and may send either casing.
    response = await client.post(
        "/api/conversation/message",
        json={
            "sessionId": SESSION,
            "message": "How crowded is gate-3?",
            "language": "en",
            "isVoice": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == SESSION


async def test_every_response_carries_a_request_id_header(client: AsyncClient) -> None:
    response = await client.post(
        "/api/conversation/message",
        json={"session_id": SESSION, "message": "hello"},
    )
    assert response.headers["x-request-id"]


async def test_message_field_is_required(client: AsyncClient) -> None:
    # The old field was `text`; sending it must not silently succeed.
    response = await client.post(
        "/api/conversation/message", json={"session_id": SESSION, "text": "hello"}
    )
    assert response.status_code == 422


async def test_empty_message_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/conversation/message", json={"session_id": SESSION, "message": "   "}
    )
    assert response.status_code == 422


# --- widgets ----------------------------------------------------------------


async def test_crowd_widget_matches_the_documented_shape(client: AsyncClient) -> None:
    body = await send(client, message="How crowded is gate-3?", language="en")

    assert widget_types(body) == ["crowd_density"]
    data = body["widgets"][0]["data"]
    assert set(data) == {
        "zone_id",
        "zone_name",
        "density",
        "status",
        "latitude",
        "longitude",
        "updated_at",
    }
    assert data["zone_id"] == "gate-3"
    assert 0 <= data["density"] <= 100
    assert data["status"] in ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
    # A rendered phrase the app prints as-is, not an ISO timestamp.
    assert "T" not in data["updated_at"]
    assert "ago" in data["updated_at"] or data["updated_at"] == "just now"


async def test_updated_at_is_localized(client: AsyncClient) -> None:
    body = await send(client, message="गर्दी किती आहे?", language="mr")
    data = body["widgets"][0]["data"]
    # A Marathi speaker should not read English next to a Marathi zone name.
    assert "ago" not in data["updated_at"]


async def test_forecast_widget_shape(client: AsyncClient) -> None:
    body = await send(
        client, message="when is the best time to visit temple-main?", language="en"
    )
    assert widget_types(body) == ["congestion_forecast"]
    data = body["widgets"][0]["data"]
    assert set(data) == {"zone_id", "zone_name", "points", "recommendation", "updated_at"}
    assert set(data["points"][0]) == {"time", "value"}


async def test_facility_widget_shape(client: AsyncClient) -> None:
    body = await send(
        client,
        message="where is a toilet?",
        language="en",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    data = body["widgets"][0]["data"]
    assert set(data) == {
        "id",
        "category",
        "name",
        "distance",
        "latitude",
        "longitude",
        "availability",
        "contact",
    }
    assert data["category"] == "toilet"
    assert "km" in data["distance"] or "m" in data["distance"]


async def test_route_question_returns_a_route_widget(client: AsyncClient) -> None:
    """Directions must work without the LLM.

    The keyword router classified `route` but had no branch for it, so this
    answered "I didn't quite catch that" while the route tool worked fine.
    """
    body = await send(
        client,
        message="show me the route to the temple",
        language="en",
        latitude=17.6790,
        longitude=75.3245,
    )
    assert widget_types(body) == ["route_guidance"]
    data = body["widgets"][0]["data"]
    assert set(data) == {
        "origin",
        "destination",
        "route_coordinates",
        "estimated_time",
        "distance",
        "avoid_areas",
    }
    assert data["estimated_time"].endswith("walk")


async def test_route_question_without_location_asks_for_it(client: AsyncClient) -> None:
    # Its own session: the shared one may already have a location pinned by an
    # earlier test, which would satisfy the request and hide the behaviour.
    body = await send(
        client,
        message="how do I get to the temple?",
        language="en",
        session_id="route-no-location",
    )
    assert body["widgets"] == []
    assert "location" in body["response_text"].lower()


async def test_temple_widget_shape(client: AsyncClient) -> None:
    body = await send(client, message="darshan timings?", language="en")
    assert widget_types(body) == ["temple_info"]
    assert set(body["widgets"][0]["data"]) == {
        "title",
        "timings",
        "rituals",
        "events",
        "description",
    }


async def test_escalation_widget_shape(client: AsyncClient) -> None:
    body = await send(client, message="I want to talk to a human", language="en")
    assert widget_types(body) == ["human_escalation"]
    assert set(body["widgets"][0]["data"]) == {
        "status",
        "message",
        "contact_available",
    }


# --- SOS --------------------------------------------------------------------


async def test_sos_asks_for_confirmation_before_dispatching(client: AsyncClient) -> None:
    body = await send(
        client,
        message="मदत करा, इथे एक माणूस बेशुद्ध पडला आहे",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )

    assert widget_types(body) == ["sos"]
    data = body["widgets"][0]["data"]
    assert set(data) == {"status", "message", "control_room_status", "timestamp"}
    # The model may raise an emergency; only a human confirmation activates one.
    assert data["status"] == "CONFIRMATION_REQUIRED"


async def test_confirm_endpoint_returns_an_activated_sos_widget(
    client: AsyncClient,
) -> None:
    await send(
        client,
        message="help me, my mother collapsed",
        language="en",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )

    # Exactly the documented body: session_id and language only.
    response = await client.post(
        "/api/conversation/sos/confirm",
        json={"session_id": SESSION, "language": "en"},
    )
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {
        "session_id",
        "message_id",
        "language",
        "response_text",
        "widgets",
    }
    assert widget_types(body) == ["sos"]
    data = body["widgets"][0]["data"]
    assert data["status"] == "ACTIVATED"
    # No Redis in this suite, so the dashboard was not reached — the card says
    # "Standing by" rather than claiming a connection that does not exist.
    assert data["control_room_status"] == "Standing by"
    # Clock time the pilgrim can read, not an ISO timestamp.
    assert data["timestamp"].endswith(("AM", "PM"))


async def test_confirm_uses_the_location_remembered_from_the_chat(
    client: AsyncClient,
) -> None:
    # The confirm body carries no coordinates, so they must come from session state.
    await send(
        client,
        message="emergency! ambulance!",
        language="en",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    body = (
        await client.post(
            "/api/conversation/sos/confirm",
            json={"session_id": SESSION, "language": "en"},
        )
    ).json()
    assert body["widgets"][0]["data"]["status"] == "ACTIVATED"


async def test_confirm_without_any_known_location_fails_loudly(
    client: AsyncClient,
) -> None:
    body = (
        await client.post(
            "/api/conversation/sos/confirm",
            json={"session_id": "no-location-session", "language": "en"},
        )
    ).json()

    data = body["widgets"][0]["data"]
    assert data["status"] == "FAILED"
    assert data["control_room_status"] == "Unreachable"
    # An emergency that cannot be dispatched must still surface the number.
    assert "112" in body["response_text"]


async def test_confirm_can_cancel(client: AsyncClient) -> None:
    await send(
        client,
        message="help me, emergency",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    body = (
        await client.post(
            "/api/conversation/sos/confirm",
            json={"session_id": SESSION, "language": "en", "confirmed": False},
        )
    ).json()
    assert body["widgets"][0]["data"]["status"] == "FAILED"
    assert body["widgets"][0]["data"]["control_room_status"] == "Cancelled"


async def test_yes_in_chat_activates_a_pending_sos(client: AsyncClient) -> None:
    await send(
        client,
        message="help me, my mother collapsed",
        language="en",
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    body = await send(client, message="yes", language="en")

    assert widget_types(body) == ["sos"]
    assert body["widgets"][0]["data"]["status"] == "ACTIVATED"


async def test_yes_without_a_pending_sos_does_not_dispatch(client: AsyncClient) -> None:
    await send(client, message="नमस्कार", session_id="quiet-session")
    body = await send(client, message="yes", session_id="quiet-session")
    assert "sos" not in widget_types(body)


async def test_unrelated_message_clears_a_stale_sos_prompt(client: AsyncClient) -> None:
    key = "stale-prompt-session"
    await send(
        client,
        message="emergency! ambulance!",
        session_id=key,
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    # The pilgrim moves on without confirming...
    await send(client, message="darshan timings?", session_id=key, language="en")
    # ...so a later "yes" must not fire the emergency they abandoned.
    body = await send(client, message="yes", session_id=key, language="en")
    assert "sos" not in widget_types(body)


# --- sessions ---------------------------------------------------------------


async def test_anonymous_session_is_created_when_id_is_unknown(
    client: AsyncClient,
) -> None:
    body = await send(client, message="hello", session_id="a-brand-new-session")
    assert body["session_id"] == "a-brand-new-session"


async def test_session_id_may_be_omitted(client: AsyncClient) -> None:
    response = await client.post(
        "/api/conversation/message", json={"message": "hello", "language": "en"}
    )
    assert response.status_code == 200
    assert response.json()["session_id"]


async def test_session_is_reused_across_turns(client: AsyncClient) -> None:
    key = "reuse-session"
    await send(client, message="नमस्कार", session_id=key)
    body = await send(client, message="मंदिरात किती गर्दी आहे?", session_id=key)
    assert body["session_id"] == key
    assert widget_types(body) == ["crowd_density"]


async def test_location_is_remembered_across_turns(client: AsyncClient) -> None:
    key = "location-memory-session"
    await send(
        client,
        message="नमस्कार",
        session_id=key,
        latitude=TEMPLE_LAT,
        longitude=TEMPLE_LON,
    )
    # No coordinates on this turn — they must come from the remembered ones.
    body = await send(client, message="where is water?", session_id=key, language="en")
    assert widget_types(body)[0] == "nearby_facility"


async def test_facility_question_without_location_asks_for_it(client: AsyncClient) -> None:
    body = await send(
        client, message="Where is the nearest toilet?", session_id="no-loc", language="en"
    )
    assert body["widgets"] == []
    # Must ask where they are — NOT claim there are no toilets nearby, which
    # would send a pilgrim walking away from one.
    assert "location" in body["response_text"].lower()
    assert "no registered facility" not in body["response_text"].lower()


# --- channels ---------------------------------------------------------------


async def test_ivr_channel_returns_no_widgets(client: AsyncClient) -> None:
    # A phone call has no screen to render a card on.
    body = await send(
        client, message="How crowded is gate-2?", language="en", channel="ivr"
    )
    assert body["widgets"] == []
    assert body["response_text"]


async def test_app_channel_is_the_default(client: AsyncClient) -> None:
    body = await send(client, message="How crowded is gate-2?", language="en")
    assert body["widgets"]


async def test_unknown_channel_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/conversation/message",
        json={"session_id": SESSION, "message": "hello", "channel": "telepathy"},
    )
    assert response.status_code == 422
