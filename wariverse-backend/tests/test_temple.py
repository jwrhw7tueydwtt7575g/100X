"""Temple info card: contract, defaults, and the admin update."""

from __future__ import annotations

from httpx import AsyncClient

from app.data.temple import DEFAULT_TEMPLE_INFO, temple_defaults

FIELDS = {"title", "timings", "rituals", "events", "description"}


async def test_matches_the_temple_widget_shape(client: AsyncClient) -> None:
    response = await client.get("/api/temple/info", params={"language": "en"})
    assert response.status_code == 200

    body = response.json()
    assert set(body) == FIELDS
    assert body["title"] == "Vitthal Temple — Pandharpur"
    assert body["timings"] == "6:00 AM – 11:00 PM"
    assert isinstance(body["rituals"], list) and body["rituals"]
    assert isinstance(body["events"], list) and body["events"]
    assert "volunteer guidance" in body["description"].lower()


async def test_rituals_name_the_four_aartis(client: AsyncClient) -> None:
    body = (await client.get("/api/temple/info", params={"language": "en"})).json()
    joined = " ".join(body["rituals"]).lower()
    for aarti in ("morning aarti", "kakad", "evening aarti", "shej"):
        assert aarti in joined


async def test_events_list_both_ekadashis(client: AsyncClient) -> None:
    body = (await client.get("/api/temple/info", params={"language": "en"})).json()
    joined = " ".join(body["events"])
    assert "Ashadhi Ekadashi" in joined
    assert "Kartiki Ekadashi" in joined


async def test_content_is_localized(client: AsyncClient) -> None:
    marathi = (await client.get("/api/temple/info", params={"language": "mr"})).json()
    english = (await client.get("/api/temple/info", params={"language": "en"})).json()

    assert marathi["title"] != english["title"]
    assert set(marathi) == set(english) == FIELDS
    assert len(marathi["rituals"]) == len(english["rituals"])


async def test_unsupported_language_falls_back(client: AsyncClient) -> None:
    response = await client.get("/api/temple/info", params={"language": "fr"})
    assert response.status_code == 200
    assert response.json()["title"]


async def test_works_without_a_database(client: AsyncClient) -> None:
    # The bundled default must still answer — timings are safety-adjacent.
    response = await client.get("/api/temple/info", params={"language": "en"})
    assert response.status_code == 200
    assert response.json()["timings"]


def test_defaults_cover_every_supported_language() -> None:
    assert set(DEFAULT_TEMPLE_INFO) == {"en", "mr", "hi"}
    for language in DEFAULT_TEMPLE_INFO:
        defaults = temple_defaults(language)
        assert set(defaults) == FIELDS
        assert defaults["rituals"] and defaults["events"]


# --- admin update -----------------------------------------------------------


async def test_admin_update_requires_a_key(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.put(
        "/api/admin/temple/info", json={"timings": "5:00 AM – 11:30 PM"}
    )
    assert response.status_code == 401


async def test_admin_update_refuses_when_no_key_configured(client: AsyncClient) -> None:
    response = await client.put(
        "/api/admin/temple/info", json={"timings": "5:00 AM – 11:30 PM"}
    )
    assert response.status_code == 503


async def test_admin_update_needs_at_least_one_field(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.put(
        "/api/admin/temple/info", json={}, headers={"X-API-Key": "the-real-key"}
    )
    assert response.status_code == 400


async def test_admin_update_needs_the_store(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "admin_api_key", "the-real-key", raising=False)
    response = await client.put(
        "/api/admin/temple/info",
        json={"timings": "5:00 AM – 11:30 PM"},
        headers={"X-API-Key": "the-real-key"},
    )
    assert response.status_code == 503
