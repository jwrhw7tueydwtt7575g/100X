"""Auth flow tests.

These run without Postgres or Redis, so they cover the contract the frontend
sees (`app/auth.tsx`), phone normalisation, and the degraded behaviour when the
code store is unreachable. The full send → verify → JWT → protected-route path
is exercised against a real database in `test_auth_integration.py`.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from jose import jwt

from app.config import settings
from app.models.schemas import _normalise_phone
from app.security import ISSUER, create_access_token, decode_access_token
from app.utils import now_utc


# --- phone validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+919876543210", "+919876543210"),  # what the frontend sends
        ("9876543210", "+919876543210"),  # bare 10 digits
        ("09876543210", "+919876543210"),  # leading zero
        ("919876543210", "+919876543210"),  # country code, no plus
        ("0091 98765 43210", "+919876543210"),  # international prefix, spaces
        ("+91 98765-43210", "+919876543210"),  # spaces and dashes
    ],
)
def test_indian_numbers_are_normalised(raw: str, expected: str) -> None:
    assert _normalise_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "+915876543210",  # Indian mobiles start 6-9
        "+91987654321",  # 9 digits
        "+9198765432100",  # 11 digits
        "+14155552671",  # not +91
        "98765abcde",
        "",
    ],
)
def test_non_indian_mobile_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="Indian mobile number"):
        _normalise_phone(raw)


async def test_send_rejects_a_non_indian_number(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/otp/send", json={"phone_number": "+14155552671"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_send_rejects_the_old_field_name(client: AsyncClient) -> None:
    # The contract is `phone_number`; `phone` must not silently work.
    response = await client.post("/api/auth/otp/send", json={"phone": "9876543210"})
    assert response.status_code == 422


# --- degraded behaviour -----------------------------------------------------


async def test_send_needs_the_code_store(client: AsyncClient) -> None:
    # Codes live in `otp_codes`; with Postgres down there would be nothing to
    # verify against, so promising "OTP sent" would be a lie.
    response = await client.post(
        "/api/auth/otp/send", json={"phone_number": "+919876543210"}
    )
    assert response.status_code == 503


async def test_verify_needs_the_user_store(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/otp/verify",
        json={"phone_number": "+919876543210", "otp": "123456"},
    )
    assert response.status_code == 503


# --- protected routes -------------------------------------------------------


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert "bearer" in response.json()["error"]["message"].lower()


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_profile_update_requires_a_token(client: AsyncClient) -> None:
    response = await client.post("/api/auth/profile/update", json={"name": "Sunita"})
    assert response.status_code == 401


def test_profile_update_rejects_an_unsupported_language() -> None:
    from app.models.schemas import ProfileUpdateRequest

    assert ProfileUpdateRequest(language="hi").language == "hi"
    with pytest.raises(ValueError):
        ProfileUpdateRequest(language="fr")


def test_profile_update_rejects_an_empty_name() -> None:
    from app.models.schemas import ProfileUpdateRequest

    with pytest.raises(ValueError):
        ProfileUpdateRequest(name="   ")


# --- JWT --------------------------------------------------------------------


def test_token_carries_user_phone_and_session() -> None:
    user_id, session_id = uuid4(), uuid4()
    token, expires_in = create_access_token(user_id, "+919876543210", session_id)

    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=ISSUER)
    assert claims["user_id"] == str(user_id)
    assert claims["phone_number"] == "+919876543210"
    assert claims["session_id"] == str(session_id)
    assert expires_in == 30 * 24 * 60 * 60  # 30 days

    payload = decode_access_token(token)
    assert payload.user_id == user_id
    assert payload.session_id == session_id


def test_expired_token_is_rejected() -> None:
    from fastapi import HTTPException

    past = now_utc() - timedelta(days=1)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "user_id": str(uuid4()),
            "phone_number": "+919876543210",
            "session_id": None,
            "iat": int((past - timedelta(days=31)).timestamp()),
            "exp": int(past.timestamp()),
            "iss": ISSUER,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(token)
    assert exc.value.status_code == 401


def test_token_signed_with_another_secret_is_rejected() -> None:
    from fastapi import HTTPException

    token = jwt.encode(
        {
            "user_id": str(uuid4()),
            "phone_number": "+919876543210",
            "exp": int((now_utc() + timedelta(days=1)).timestamp()),
            "iss": ISSUER,
        },
        "an-attackers-secret",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        decode_access_token(token)


def test_alg_none_token_is_rejected() -> None:
    """An unsigned token must never authenticate (CVE-2024-33663 class).

    The token is hand-assembled because python-jose refuses to *encode* with
    `alg=none` at all — this checks the decode side, which is where a forged
    token would actually arrive.
    """
    import base64
    import json

    from fastapi import HTTPException

    def segment(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    unsigned = "{}.{}.".format(
        segment({"alg": "none", "typ": "JWT"}),
        segment(
            {
                "user_id": str(uuid4()),
                "phone_number": "+919876543210",
                "exp": int((now_utc() + timedelta(days=1)).timestamp()),
                "iss": ISSUER,
            }
        ),
    )
    with pytest.raises(HTTPException):
        decode_access_token(unsigned)


def test_token_from_a_different_issuer_is_rejected() -> None:
    from fastapi import HTTPException

    token = jwt.encode(
        {
            "user_id": str(uuid4()),
            "phone_number": "+919876543210",
            "exp": int((now_utc() + timedelta(days=1)).timestamp()),
            "iss": "someone-else",
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException):
        decode_access_token(token)


# --- OTP generation ---------------------------------------------------------


def test_otp_is_six_digits_and_not_repeating() -> None:
    from app.utils import generate_otp

    codes = {generate_otp(6) for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    # A trivially broken generator would collide constantly across 200 draws.
    assert len(codes) > 150


def test_sms_message_names_the_service_and_expiry() -> None:
    from app.services.sms import otp_message

    message = otp_message("123456", 10)
    assert "123456" in message
    assert "WariVerse" in message
    assert "10 minutes" in message
    assert "not share" in message.lower()
