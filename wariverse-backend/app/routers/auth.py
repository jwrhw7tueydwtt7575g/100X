"""Phone + OTP authentication.

The OTP itself is never stored in plaintext and never written to the logs: only
an HMAC of `phone:otp` keyed with JWT_SECRET is kept, in Redis, under the
configured TTL. Postgres keeps a delivery audit row without the code.

There is no SMS provider wired in — `_deliver_otp` is the single seam where one
(MSG91, Gupshup, Twilio) plugs in. Outside production the code is echoed in the
response so the app can be exercised end to end.
"""

from __future__ import annotations

import hmac
import json
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession
from app.models.db_models import OtpRequest, User
from app.models.schemas import (
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    TokenResponse,
    UserOut,
)
from app.redis_client import get_redis
from app.security import create_access_token
from app.utils import generate_otp, now_utc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_PREFIX = "wv:otp:"
COOLDOWN_PREFIX = "wv:otp:cooldown:"
RESEND_COOLDOWN_SECONDS = 30

# Dev-only fallback so the flow works before Redis is provisioned.
_memory_otp: dict[str, dict[str, Any]] = {}


@router.post(
    "/otp/send",
    response_model=OtpSendResponse,
    summary="Send a login OTP to a phone number",
    responses={429: {"description": "Requested again too soon"}},
)
async def send_otp(payload: OtpSendRequest, db: DbSession) -> OtpSendResponse:
    phone = payload.phone

    if await _in_cooldown(phone):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"please wait {RESEND_COOLDOWN_SECONDS}s before requesting another code",
        )

    otp = generate_otp(settings.otp_length)
    request_id = uuid4().hex
    record = {
        "request_id": request_id,
        "hash": _hash_otp(phone, otp),
        "attempts": 0,
        "language": payload.language or settings.default_language,
    }
    await _store_otp(phone, record)
    await _set_cooldown(phone)
    await _audit_send(db, phone)
    await _deliver_otp(phone, otp)

    log.info("otp_sent", phone=_mask(phone), request_id=request_id)

    return OtpSendResponse(
        request_id=request_id,
        phone=phone,
        expires_in_seconds=settings.otp_ttl_seconds,
        resend_after_seconds=RESEND_COOLDOWN_SECONDS,
        debug_otp=otp if settings.expose_debug_otp else None,
    )


@router.post(
    "/otp/verify",
    response_model=TokenResponse,
    summary="Verify an OTP and receive an access token",
    responses={
        400: {"description": "Invalid or expired code"},
        429: {"description": "Too many attempts"},
        503: {"description": "User store unavailable"},
    },
)
async def verify_otp(payload: OtpVerifyRequest, db: DbSession) -> TokenResponse:
    phone = payload.phone
    record = await _load_otp(phone)

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no active code for this number; request a new one",
        )

    if record.get("attempts", 0) >= settings.otp_max_attempts:
        await _clear_otp(phone)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many incorrect attempts; request a new code",
        )

    if not hmac.compare_digest(record["hash"], _hash_otp(phone, payload.otp)):
        record["attempts"] = record.get("attempts", 0) + 1
        await _store_otp(phone, record, keep_ttl=True)
        log.info("otp_verify_failed", phone=_mask(phone), attempts=record["attempts"])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="incorrect code"
        )

    await _clear_otp(phone)

    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user store unavailable; please retry shortly",
        )

    user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
    language = payload.preferred_language or record.get("language") or settings.default_language

    if user is None:
        user = User(
            phone=phone,
            display_name=payload.display_name,
            preferred_language=language,
            last_login_at=now_utc(),
        )
        db.add(user)
    else:
        user.last_login_at = now_utc()
        if payload.display_name:
            user.display_name = payload.display_name
        if payload.preferred_language:
            user.preferred_language = payload.preferred_language

    await _mark_verified(db, phone)
    await db.commit()
    await db.refresh(user)

    token, expires_in = create_access_token(user.id, user.phone)
    log.info("otp_verified", phone=_mask(phone), user_id=str(user.id))

    return TokenResponse(
        access_token=token,
        expires_in_seconds=expires_in,
        user=UserOut.model_validate(user),
    )


# --- OTP storage ------------------------------------------------------------


def _hash_otp(phone: str, otp: str) -> str:
    return hmac.new(
        settings.jwt_secret.encode(), f"{phone}:{otp}".encode(), sha256
    ).hexdigest()


async def _store_otp(phone: str, record: dict[str, Any], keep_ttl: bool = False) -> None:
    key = f"{OTP_PREFIX}{phone}"
    client = get_redis()
    if client is not None:
        try:
            # `expires_at` only exists on the in-process fallback records; Redis
            # owns expiry via the key TTL.
            payload = {k: v for k, v in record.items() if k != "expires_at"}
            await client.set(
                key,
                json.dumps(payload),
                ex=None if keep_ttl else settings.otp_ttl_seconds,
                keepttl=keep_ttl,
            )
            return
        except (RedisError, OSError) as exc:
            log.warning("otp_redis_write_failed", error=str(exc))

    _memory_otp[key] = {**record, "expires_at": now_utc() + timedelta(
        seconds=settings.otp_ttl_seconds
    )}


async def _load_otp(phone: str) -> dict[str, Any] | None:
    key = f"{OTP_PREFIX}{phone}"
    client = get_redis()
    if client is not None:
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw else None
        except (RedisError, OSError, json.JSONDecodeError) as exc:
            log.warning("otp_redis_read_failed", error=str(exc))

    record = _memory_otp.get(key)
    if record is None:
        return None
    if record["expires_at"] < now_utc():
        _memory_otp.pop(key, None)
        return None
    return record


async def _clear_otp(phone: str) -> None:
    key = f"{OTP_PREFIX}{phone}"
    _memory_otp.pop(key, None)
    client = get_redis()
    if client is not None:
        try:
            await client.delete(key)
        except (RedisError, OSError) as exc:
            log.warning("otp_redis_delete_failed", error=str(exc))


async def _in_cooldown(phone: str) -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await client.exists(f"{COOLDOWN_PREFIX}{phone}"))
    except (RedisError, OSError):
        return False


async def _set_cooldown(phone: str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        await client.set(f"{COOLDOWN_PREFIX}{phone}", "1", ex=RESEND_COOLDOWN_SECONDS)
    except (RedisError, OSError) as exc:
        log.warning("otp_cooldown_set_failed", error=str(exc))


# --- delivery & audit -------------------------------------------------------


async def _deliver_otp(phone: str, otp: str) -> None:
    """Hand the code to an SMS provider.

    Intentionally a no-op: wire the provider here (MSG91/Gupshup/Twilio) and
    keep the code out of the logs. `debug_otp` in the response covers local
    development.
    """
    log.info("otp_delivery_stub", phone=_mask(phone), channel="sms")


async def _audit_send(db, phone: str) -> None:
    if db is None:
        return
    try:
        db.add(
            OtpRequest(
                phone=phone,
                channel="sms",
                expires_at=now_utc() + timedelta(seconds=settings.otp_ttl_seconds),
            )
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — auditing must not block login
        await db.rollback()
        log.warning("otp_audit_failed", error=str(exc))


async def _mark_verified(db, phone: str) -> None:
    if db is None:
        return
    try:
        row = (
            await db.execute(
                select(OtpRequest)
                .where(OtpRequest.phone == phone, OtpRequest.verified_at.is_(None))
                .order_by(OtpRequest.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            row.verified_at = now_utc()
    except Exception as exc:  # noqa: BLE001
        log.warning("otp_audit_verify_failed", error=str(exc))


def _mask(phone: str) -> str:
    return f"{phone[:3]}****{phone[-3:]}" if len(phone) > 6 else "****"
