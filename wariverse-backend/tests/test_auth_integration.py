"""End-to-end auth against a real Postgres and Redis.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`. Run:

    WARIVERSE_TEST_DATABASE_URL=postgresql+asyncpg://wariverse:wariverse@localhost:5432/wariverse \
    WARIVERSE_TEST_REDIS_URL=redis://localhost:6379/0 \
    pytest tests/test_auth_integration.py

Every test uses a distinct phone number so runs do not interfere through the
per-number rate limiter.
"""

from __future__ import annotations

import random

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select, text

from app.config import settings
from app.models.db_models import OtpCode, Session, User
from app.security import ISSUER
from tests.conftest import INTEGRATION_DB_URL

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)


def a_phone() -> str:
    """A random, valid Indian mobile number, unique per test."""
    return f"+91{random.choice('6789')}{random.randint(0, 10**9 - 1):09d}"


async def _db():
    from app.db import get_engine
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def _reset_rate_limit(phone: str) -> None:
    from app.redis_client import get_redis

    client = get_redis()
    if client is not None:
        await client.delete(f"wv:otp:rate:{phone}", f"wv:otp:attempts:{phone}")


# --- happy path -------------------------------------------------------------


async def test_full_signup_flow(live_client: AsyncClient) -> None:
    phone = a_phone()

    sent = await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    assert sent.status_code == 200
    body = sent.json()
    assert body == {"success": True, "message": "OTP sent", "demo_otp": body["demo_otp"]}
    assert len(body["demo_otp"]) == 6 and body["demo_otp"].isdigit()

    verified = await live_client.post(
        "/api/auth/otp/verify", json={"phone_number": phone, "otp": body["demo_otp"]}
    )
    assert verified.status_code == 200

    payload = verified.json()
    assert payload["success"] is True
    assert set(payload["user"]) == {"id", "phone_number", "name", "language"}
    assert payload["user"]["phone_number"] == phone
    assert payload["user"]["name"] is None

    claims = jwt.decode(
        payload["token"], settings.jwt_secret, algorithms=["HS256"], issuer=ISSUER
    )
    assert claims["user_id"] == payload["user"]["id"]
    assert claims["phone_number"] == phone
    assert claims["session_id"], "the JWT must carry the session it created"

    # The session in the token is a real row.
    async with await _db() as db:
        session = (
            await db.execute(select(Session).where(Session.id == claims["session_id"]))
        ).scalar_one()
        assert str(session.user_id) == payload["user"]["id"]

        user = (
            await db.execute(select(User).where(User.phone_number == phone))
        ).scalar_one()
        assert user.is_verified is True


async def test_returning_user_is_upserted_not_duplicated(live_client: AsyncClient) -> None:
    phone = a_phone()

    for _ in range(2):
        await _reset_rate_limit(phone)
        sent = await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
        otp = sent.json()["demo_otp"]
        verified = await live_client.post(
            "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
        )
        assert verified.status_code == 200

    async with await _db() as db:
        count = len(
            (await db.execute(select(User).where(User.phone_number == phone)))
            .scalars()
            .all()
        )
    assert count == 1


# --- OTP lifecycle ----------------------------------------------------------


async def test_used_code_cannot_be_replayed(live_client: AsyncClient) -> None:
    phone = a_phone()
    otp = (
        await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    ).json()["demo_otp"]

    first = await live_client.post(
        "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
    )
    assert first.status_code == 200

    replay = await live_client.post(
        "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
    )
    assert replay.status_code == 400

    async with await _db() as db:
        record = (
            await db.execute(select(OtpCode).where(OtpCode.phone_number == phone))
        ).scalar_one()
        assert record.used is True


async def test_expired_code_is_rejected(live_client: AsyncClient) -> None:
    phone = a_phone()
    otp = (
        await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    ).json()["demo_otp"]

    # Age the code past its 10-minute window.
    async with await _db() as db:
        await db.execute(
            text(
                "UPDATE otp_codes SET expires_at = now() - interval '1 minute' "
                "WHERE phone_number = :p"
            ),
            {"p": phone},
        )
        await db.commit()

    response = await live_client.post(
        "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
    )
    assert response.status_code == 400
    assert "no active code" in response.json()["error"]["message"]


async def test_wrong_code_is_rejected(live_client: AsyncClient) -> None:
    phone = a_phone()
    otp = (
        await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    ).json()["demo_otp"]
    wrong = "000000" if otp != "000000" else "111111"

    response = await live_client.post(
        "/api/auth/otp/verify", json={"phone_number": phone, "otp": wrong}
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "incorrect code"


async def test_code_is_stored_for_the_configured_ten_minutes(
    live_client: AsyncClient,
) -> None:
    phone = a_phone()
    await live_client.post("/api/auth/otp/send", json={"phone_number": phone})

    async with await _db() as db:
        record = (
            await db.execute(select(OtpCode).where(OtpCode.phone_number == phone))
        ).scalar_one()
        window = (record.expires_at - record.created_at).total_seconds()
    assert 590 <= window <= 610  # 10 minutes, allowing for clock skew


# --- rate limiting ----------------------------------------------------------


async def test_fourth_code_in_an_hour_is_refused(live_client: AsyncClient) -> None:
    phone = a_phone()
    await _reset_rate_limit(phone)

    for attempt in range(settings.otp_rate_limit):
        response = await live_client.post(
            "/api/auth/otp/send", json={"phone_number": phone}
        )
        assert response.status_code == 200, f"send {attempt + 1} should succeed"

    blocked = await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    assert blocked.status_code == 429
    assert "too many codes" in blocked.json()["error"]["message"]


async def test_rate_limit_holds_when_redis_is_down(live_client: AsyncClient) -> None:
    """The Postgres fallback must enforce the same limit.

    Failing open here would turn the send endpoint into an SMS-bombing tool.
    """
    import app.redis_client as redis_client

    phone = a_phone()
    await _reset_rate_limit(phone)

    healthy = redis_client._healthy
    redis_client._healthy = False  # simulate an outage
    try:
        for attempt in range(settings.otp_rate_limit):
            response = await live_client.post(
                "/api/auth/otp/send", json={"phone_number": phone}
            )
            assert response.status_code == 200, f"send {attempt + 1} should succeed"

        blocked = await live_client.post(
            "/api/auth/otp/send", json={"phone_number": phone}
        )
        assert blocked.status_code == 429
    finally:
        redis_client._healthy = healthy


# --- protected routes -------------------------------------------------------


async def _sign_in(live_client: AsyncClient) -> tuple[str, dict]:
    phone = a_phone()
    otp = (
        await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    ).json()["demo_otp"]
    body = (
        await live_client.post(
            "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
        )
    ).json()
    return body["token"], body["user"]


async def test_me_returns_the_signed_in_pilgrim(live_client: AsyncClient) -> None:
    token, user = await _sign_in(live_client)

    response = await live_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    me = response.json()
    assert me["id"] == user["id"]
    assert me["phone_number"] == user["phone_number"]
    assert me["is_verified"] is True


async def test_profile_update_persists(live_client: AsyncClient) -> None:
    token, _ = await _sign_in(live_client)
    headers = {"Authorization": f"Bearer {token}"}

    updated = await live_client.post(
        "/api/auth/profile/update",
        json={"name": "Sunita Pawar", "language": "hi"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["success"] is True
    assert updated.json()["user"]["name"] == "Sunita Pawar"

    me = (await live_client.get("/api/auth/me", headers=headers)).json()
    assert me["name"] == "Sunita Pawar"
    assert me["language"] == "hi"


async def test_profile_update_accepts_one_field(live_client: AsyncClient) -> None:
    token, _ = await _sign_in(live_client)
    headers = {"Authorization": f"Bearer {token}"}

    response = await live_client.post(
        "/api/auth/profile/update", json={"language": "en"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["user"]["language"] == "en"
    assert response.json()["user"]["name"] is None


async def test_profile_update_needs_at_least_one_field(live_client: AsyncClient) -> None:
    token, _ = await _sign_in(live_client)
    response = await live_client.post(
        "/api/auth/profile/update",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


async def test_token_for_a_deleted_user_is_rejected(live_client: AsyncClient) -> None:
    token, user = await _sign_in(live_client)

    async with await _db() as db:
        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": user["id"]})
        await db.commit()

    response = await live_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
