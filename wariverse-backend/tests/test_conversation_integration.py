"""Conversation behaviour that needs real Redis and Postgres.

Skipped unless `WARIVERSE_TEST_DATABASE_URL` is set — see `conftest.py`.
"""

from __future__ import annotations

import random

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.models.db_models import Message, Session
from app.routers.conversation import RATE_LIMIT_MESSAGES
from tests.conftest import INTEGRATION_DB_URL, TEMPLE_LAT, TEMPLE_LON

pytestmark = pytest.mark.skipif(
    not INTEGRATION_DB_URL, reason="set WARIVERSE_TEST_DATABASE_URL to run"
)


def a_session() -> str:
    return f"test-session-{random.randint(0, 10**9)}"


async def _db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import get_engine

    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)()


async def _clear_rate_limit(*keys: str) -> None:
    from app.redis_client import get_redis

    client = get_redis()
    if client is not None:
        await client.delete(*(f"wv:chat:rate:{k}" for k in keys))


async def _sign_in(live_client: AsyncClient) -> tuple[str, str]:
    """Returns (bearer token, user id)."""
    phone = f"+91{random.choice('6789')}{random.randint(0, 10**9 - 1):09d}"
    otp = (
        await live_client.post("/api/auth/otp/send", json={"phone_number": phone})
    ).json()["demo_otp"]
    body = (
        await live_client.post(
            "/api/auth/otp/verify", json={"phone_number": phone, "otp": otp}
        )
    ).json()
    return body["token"], body["user"]["id"]


# --- persistence ------------------------------------------------------------


async def test_turn_is_persisted_against_the_client_session_key(
    live_client: AsyncClient,
) -> None:
    key = a_session()
    await _clear_rate_limit(key)

    body = (
        await live_client.post(
            "/api/conversation/message",
            json={"session_id": key, "message": "How crowded is gate-3?", "language": "en"},
        )
    ).json()
    assert body["session_id"] == key

    async with await _db() as db:
        # The client's literal lives in session_token; the PK stays a UUID.
        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one()

        messages = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session.id)
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].widgets_json[0]["type"] == "crowd_density"


async def test_same_client_key_resumes_one_session(live_client: AsyncClient) -> None:
    key = a_session()
    await _clear_rate_limit(key)

    for _ in range(3):
        await live_client.post(
            "/api/conversation/message",
            json={"session_id": key, "message": "hello", "language": "en"},
        )

    async with await _db() as db:
        rows = (
            (await db.execute(select(Session).where(Session.session_token == key)))
            .scalars()
            .all()
        )
    assert len(rows) == 1, "one client key must not spawn multiple sessions"


# --- rate limiting ----------------------------------------------------------


async def test_thirty_first_message_in_a_minute_is_refused(
    live_client: AsyncClient,
) -> None:
    key = a_session()
    await _clear_rate_limit(key)

    for i in range(RATE_LIMIT_MESSAGES):
        response = await live_client.post(
            "/api/conversation/message",
            json={"session_id": key, "message": "hello", "language": "en"},
        )
        assert response.status_code == 200, f"message {i + 1} should be allowed"

    blocked = await live_client.post(
        "/api/conversation/message",
        json={"session_id": key, "message": "hello", "language": "en"},
    )
    assert blocked.status_code == 429
    # A rate-limited pilgrim in trouble still needs the number.
    assert "112" in blocked.json()["error"]["message"]


async def test_rate_limit_is_per_session(live_client: AsyncClient) -> None:
    busy, quiet = a_session(), a_session()
    await _clear_rate_limit(busy, quiet)

    for _ in range(RATE_LIMIT_MESSAGES + 1):
        await live_client.post(
            "/api/conversation/message",
            json={"session_id": busy, "message": "hello", "language": "en"},
        )

    # One noisy session must not lock out everyone else.
    other = await live_client.post(
        "/api/conversation/message",
        json={"session_id": quiet, "message": "hello", "language": "en"},
    )
    assert other.status_code == 200


async def test_chat_rate_limit_fails_open_when_redis_is_down(
    live_client: AsyncClient,
) -> None:
    """Deliberately the opposite of the OTP limiter.

    An OTP send costs money and enables SMS bombing, so that one fails closed.
    Refusing a pilgrim's question about a medical post because the cache
    blipped is the worse outcome, so this one fails open.
    """
    import app.redis_client as redis_client

    key = a_session()
    healthy = redis_client._healthy
    redis_client._healthy = False
    try:
        for _ in range(RATE_LIMIT_MESSAGES + 5):
            response = await live_client.post(
                "/api/conversation/message",
                json={"session_id": key, "message": "hello", "language": "en"},
            )
            assert response.status_code == 200
    finally:
        redis_client._healthy = healthy


# --- authentication ---------------------------------------------------------


async def test_authenticated_session_is_linked_to_the_user(
    live_client: AsyncClient,
) -> None:
    token, user_id = await _sign_in(live_client)
    key = a_session()
    await _clear_rate_limit(key, f"u:{user_id}:{key}")

    await live_client.post(
        "/api/conversation/message",
        json={"session_id": key, "message": "hello", "language": "en"},
        headers={"Authorization": f"Bearer {token}"},
    )

    async with await _db() as db:
        session = (
            await db.execute(
                select(Session).where(Session.session_token == f"u:{user_id}:{key}")
            )
        ).scalar_one()
    assert str(session.user_id) == user_id


async def test_two_pilgrims_sharing_a_session_id_do_not_collide(
    live_client: AsyncClient,
) -> None:
    """The frontend ships one hard-coded session id for every install.

    Authenticated callers are scoped by user id, so their transcripts stay
    separate even when they send the identical literal. Anonymous callers are
    NOT protected — see the warning in app/routers/conversation.py.
    """
    shared = "wariverse-session"
    token_a, user_a = await _sign_in(live_client)
    token_b, user_b = await _sign_in(live_client)
    await _clear_rate_limit(f"u:{user_a}:{shared}", f"u:{user_b}:{shared}")

    await live_client.post(
        "/api/conversation/message",
        json={"session_id": shared, "message": "How crowded is gate-1?", "language": "en"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    await live_client.post(
        "/api/conversation/message",
        json={"session_id": shared, "message": "How crowded is gate-2?", "language": "en"},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    async with await _db() as db:
        rows = (
            (
                await db.execute(
                    select(Session).where(
                        Session.session_token.in_(
                            [f"u:{user_a}:{shared}", f"u:{user_b}:{shared}"]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len({r.id for r in rows}) == 2
    assert {str(r.user_id) for r in rows} == {user_a, user_b}


async def test_one_pilgrims_sos_is_not_confirmable_by_another(
    live_client: AsyncClient,
) -> None:
    """A shared literal must not let B's "yes" dispatch A's emergency."""
    shared = "wariverse-session"
    token_a, user_a = await _sign_in(live_client)
    token_b, user_b = await _sign_in(live_client)
    await _clear_rate_limit(f"u:{user_a}:{shared}", f"u:{user_b}:{shared}")

    raised = await live_client.post(
        "/api/conversation/message",
        json={
            "session_id": shared,
            "message": "help me, my mother collapsed",
            "language": "en",
            "latitude": TEMPLE_LAT,
            "longitude": TEMPLE_LON,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert raised.json()["widgets"][0]["data"]["status"] == "CONFIRMATION_REQUIRED"

    # B says "yes" with the same session id but a different token.
    body = (
        await live_client.post(
            "/api/conversation/message",
            json={"session_id": shared, "message": "yes", "language": "en"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
    ).json()
    assert "sos" not in [w["type"] for w in body["widgets"]]


async def test_invalid_token_falls_back_to_anonymous(live_client: AsyncClient) -> None:
    key = a_session()
    await _clear_rate_limit(key)

    # Chat must stay open: safety information is not gated behind a login.
    response = await live_client.post(
        "/api/conversation/message",
        json={"session_id": key, "message": "hello", "language": "en"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 200

    async with await _db() as db:
        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one()
    assert session.user_id is None


# --- history ----------------------------------------------------------------


async def test_sos_confirmation_reaches_the_llm_replay_history(
    live_client: AsyncClient,
) -> None:
    """The confirmation turn bypasses the orchestrator, so it is mirrored in.

    Without it the model would answer the next question unaware that an
    emergency had just been dispatched.
    """
    from app.redis_client import get_redis

    key = a_session()
    await _clear_rate_limit(key)

    await live_client.post(
        "/api/conversation/message",
        json={
            "session_id": key,
            "message": "help me, my mother collapsed",
            "language": "en",
            "latitude": TEMPLE_LAT,
            "longitude": TEMPLE_LON,
        },
    )
    await live_client.post(
        "/api/conversation/message",
        json={"session_id": key, "message": "yes", "language": "en"},
    )

    async with await _db() as db:
        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one()

    entries = await get_redis().lrange(f"session:{session.id}:history", 0, -1)
    assert len(entries) == 4, "both turns must be in the replay history"


async def test_history_is_capped_at_ten_messages(live_client: AsyncClient) -> None:
    from app.redis_client import get_redis

    key = a_session()
    await _clear_rate_limit(key)

    for i in range(8):
        await live_client.post(
            "/api/conversation/message",
            json={"session_id": key, "message": f"hello {i}", "language": "en"},
        )

    async with await _db() as db:
        session = (
            await db.execute(select(Session).where(Session.session_token == key))
        ).scalar_one()
        message_count = (
            await db.execute(
                text("SELECT count(*) FROM messages WHERE session_id = :s"),
                {"s": str(session.id)},
            )
        ).scalar_one()

    client = get_redis()
    entries = await client.lrange(f"session:{session.id}:history", 0, -1)

    assert message_count == 16, "every turn is kept in the durable transcript"
    assert len(entries) == 10, "the replay window stays capped"
