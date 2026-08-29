from httpx import AsyncClient


async def test_health_returns_exact_contract(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.0.0"}


async def test_readiness_reports_downed_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "degraded"
    statuses = {c["name"]: c["status"] for c in body["components"]}
    assert statuses["database"] == "down"
    assert statuses["redis"] == "down"
    assert statuses["llm"] == "disabled"


async def test_request_id_header_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


async def test_cors_allows_expo_dev_origin(client: AsyncClient) -> None:
    response = await client.options(
        "/api/temple/info",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"


async def test_cors_allows_wariverse_subdomains(client: AsyncClient) -> None:
    response = await client.options(
        "/api/temple/info",
        headers={
            "Origin": "https://app.wariverse.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.wariverse.app"


async def test_cors_rejects_unknown_origin(client: AsyncClient) -> None:
    response = await client.options(
        "/api/temple/info",
        headers={
            "Origin": "https://wariverse.app.evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


async def test_validation_error_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/facilities/nearby", params={"lat": 200, "lon": 75.33})
    assert response.status_code == 422

    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["request_id"]
