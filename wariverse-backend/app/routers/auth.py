"""Phone + OTP authentication.

Flow: `POST /otp/send` → SMS → `POST /otp/verify` → JWT → authenticated calls.

SECURITY — read before using this with real phone numbers.

`otp_codes.code` stores the six digits in plaintext, per the data spec. Read
access to that table (a replica, a backup, a dump, a SQL injection) is enough
to log in as any pilgrim with a code in flight. The fix needs no schema change:
store `hash_otp(phone_number, code)` — implemented below — widen the column to
`String(64)`, and compare digests in `verify_otp`. Recommended before launch.

Codes reach the logs only when the API is also returning them as `demo_otp`
(i.e. outside production). In production nothing logs a code.
"""

from __future__ import annotations

import hmac
from datetime import timedelta
from hashlib import sha256

import structlog
from fastapi import APIRouter, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import DbSession, get_session_service
from app.models.db_models import OtpCode, User
from app.models.schemas import (
    OtpSendRequest,
    OtpSendResponse,
    OtpVerifyRequest,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    TokenResponse,
    UserOut,
    UserProfile,
)
from app.redis_client import get_redis
from app.security import CurrentUser, create_access_token
from app.services.sms import send_otp_sms
from app.utils import generate_otp, now_utc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

RATE_PREFIX = "wv:otp:rate:"
ATTEMPTS_PREFIX = "wv:otp:attempts:"


# --- send -------------------------------------------------------------------


@router.post(
    "/otp/send",
    response_model=OtpSendResponse,
    summary="Send a login OTP to an Indian mobile number",
    responses={
        429: {"description": "Rate limit reached for this number"},
        503: {"description": "Code store unavailable"},
    },
)
async def send_otp(payload: OtpSendRequest, db: DbSession) -> OtpSendResponse:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="code store unavailable; please retry shortly",
        )

    phone_number = payload.phone_number

    sent_recently = await _recent_send_count(phone_number, db)
    if sent_recently >= settings.otp_rate_limit:
        minutes = max(1, settings.otp_rate_window_seconds // 60)
        log.warning("otp_rate_limited", phone=_mask(phone_number), count=sent_recently)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"too many codes requested; try again in {minutes} minutes "
                f"(limit {settings.otp_rate_limit} per {minutes} minutes)"
            ),
        )

    code = generate_otp(settings.otp_length)
    db.add(
        OtpCode(
            phone_number=phone_number,
            code=code,
            expires_at=now_utc() + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    await db.commit()

    await _register_send(phone_number)
    await _reset_attempts(phone_number)

    delivered = await send_otp_sms(phone_number, code)
    log.info(
        "otp_sent",
        phone=_mask(phone_number),
        provider=settings.sms_provider,
        delivered=delivered,
        sends_in_window=sent_recently + 1,
    )

    return OtpSendResponse(
        success=True,
        message="OTP sent",
        demo_otp=code if settings.expose_debug_otp else None,
    )


# --- verify -----------------------------------------------------------------


@router.post(
    "/otp/verify",
    response_model=TokenResponse,
    summary="Verify an OTP and receive a 30-day JWT",
    responses={
        400: {"description": "Invalid or expired code"},
        429: {"description": "Too many incorrect attempts"},
        503: {"description": "User store unavailable"},
    },
)
async def verify_otp(payload: OtpVerifyRequest, db: DbSession) -> TokenResponse:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user store unavailable; please retry shortly",
        )

    phone_number = payload.phone_number

    if await _attempts(phone_number) >= settings.otp_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many incorrect attempts; request a new code",
        )

    record = (
        await db.execute(
            select(OtpCode)
            .where(
                OtpCode.phone_number == phone_number,
                OtpCode.used.is_(False),
                OtpCode.expires_at > now_utc(),
            )
            .order_by(desc(OtpCode.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no active code for this number; request a new one",
        )

    # compare_digest even on plaintext: the comparison itself must not leak the
    # code one character at a time through timing.
    if not hmac.compare_digest(record.code, payload.otp):
        attempts = await _bump_attempts(phone_number)
        log.info("otp_verify_failed", phone=_mask(phone_number), attempts=attempts)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="incorrect code")

    record.used = True

    # Upsert the pilgrim.
    user = (
        await db.execute(select(User).where(User.phone_number == phone_number))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            phone_number=phone_number,
            language=settings.default_language,
            is_verified=True,
        )
        db.add(user)
    else:
        user.is_verified = True

    await db.flush()

    # A session is created here so the JWT can carry `session_id` — the token
    # then identifies both the pilgrim and the conversation they are in.
    sessions = get_session_service(db)
    state = await sessions.get_or_create(user_id=user.id, language=user.language)
    await sessions.ensure_row(state)

    await db.commit()
    await db.refresh(user)
    await _reset_attempts(phone_number)

    token, expires_in = create_access_token(
        user_id=user.id, phone_number=user.phone_number, session_id=state.session_id
    )
    log.info(
        "otp_verified",
        phone=_mask(phone_number),
        user_id=str(user.id),
        session_id=str(state.session_id),
        expires_in_seconds=expires_in,
    )

    return TokenResponse(success=True, token=token, user=UserOut.model_validate(user))


# --- profile ----------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserProfile,
    summary="Current user profile",
    responses={401: {"description": "Missing or invalid bearer token"}},
)
async def get_me(user: CurrentUser) -> UserProfile:
    return UserProfile.model_validate(user)


@router.post(
    "/profile/update",
    response_model=ProfileUpdateResponse,
    summary="Update display name and language preference",
    responses={
        400: {"description": "Nothing to update"},
        401: {"description": "Missing or invalid bearer token"},
    },
)
async def update_profile(
    payload: ProfileUpdateRequest, user: CurrentUser, db: DbSession
) -> ProfileUpdateResponse:
    if payload.name is None and payload.language is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide at least one of: name, language",
        )

    if payload.name is not None:
        user.name = payload.name
    if payload.language is not None:
        user.language = payload.language

    await db.commit()
    await db.refresh(user)

    log.info(
        "profile_updated",
        user_id=str(user.id),
        fields=[f for f in ("name", "language") if getattr(payload, f) is not None],
    )
    return ProfileUpdateResponse(success=True, user=UserProfile.model_validate(user))


# --- rate limiting ----------------------------------------------------------


async def _recent_send_count(phone_number: str, db: AsyncSession) -> int:
    """Codes sent to this number inside the rate window.

    Redis is the fast path. When it is down the count comes from `otp_codes`
    instead — failing open would turn an unmetered send endpoint into an
    SMS-bombing tool, and the rows are already there to count.
    """
    client = get_redis()
    if client is not None:
        try:
            raw = await client.get(f"{RATE_PREFIX}{phone_number}")
            return int(raw) if raw else 0
        except (RedisError, OSError, ValueError) as exc:
            log.warning("otp_rate_redis_read_failed", error=str(exc))

    window_start = now_utc() - timedelta(seconds=settings.otp_rate_window_seconds)
    try:
        return (
            await db.execute(
                select(func.count())
                .select_from(OtpCode)
                .where(
                    OtpCode.phone_number == phone_number,
                    OtpCode.created_at >= window_start,
                )
            )
        ).scalar_one()
    except Exception as exc:  # noqa: BLE001
        log.error("otp_rate_db_fallback_failed", error=str(exc))
        return 0


async def _register_send(phone_number: str) -> None:
    """Increment the window counter, setting its TTL on the first send."""
    client = get_redis()
    if client is None:
        return
    try:
        key = f"{RATE_PREFIX}{phone_number}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, settings.otp_rate_window_seconds)
    except (RedisError, OSError) as exc:
        log.warning("otp_rate_register_failed", error=str(exc))


async def _attempts(phone_number: str) -> int:
    """Failed-attempt count. Throttling degrades to off if Redis is down."""
    client = get_redis()
    if client is None:
        return 0
    try:
        raw = await client.get(f"{ATTEMPTS_PREFIX}{phone_number}")
        return int(raw) if raw else 0
    except (RedisError, OSError, ValueError):
        return 0


async def _bump_attempts(phone_number: str) -> int:
    client = get_redis()
    if client is None:
        return 0
    try:
        key = f"{ATTEMPTS_PREFIX}{phone_number}"
        count = await client.incr(key)
        await client.expire(key, settings.otp_ttl_seconds)
        return int(count)
    except (RedisError, OSError) as exc:
        log.warning("otp_attempts_bump_failed", error=str(exc))
        return 0


async def _reset_attempts(phone_number: str) -> None:
    client = get_redis()
    if client is None:
        return
    try:
        await client.delete(f"{ATTEMPTS_PREFIX}{phone_number}")
    except (RedisError, OSError):
        pass


# --- helpers ----------------------------------------------------------------


def hash_otp(phone_number: str, code: str) -> str:
    """Keyed digest of a code. See the module docstring for why this matters."""
    return hmac.new(
        settings.jwt_secret.encode(), f"{phone_number}:{code}".encode(), sha256
    ).hexdigest()


def _mask(phone_number: str) -> str:
    return (
        f"{phone_number[:3]}****{phone_number[-3:]}" if len(phone_number) > 6 else "****"
    )
