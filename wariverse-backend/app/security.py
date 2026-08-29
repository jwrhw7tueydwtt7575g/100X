"""JWT issuing/verification (python-jose, HS256) and the bearer dependencies.

A token carries `user_id`, `phone_number` and `session_id`, so a request can be
attributed to both a pilgrim and the conversation they were in without a
database round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select

from app.config import settings
from app.deps import DbSession
from app.models.db_models import User
from app.utils import now_utc

log = structlog.get_logger(__name__)

ISSUER = "wariverse"

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


@dataclass(slots=True)
class TokenPayload:
    user_id: UUID
    phone_number: str
    session_id: UUID | None
    issued_at: int
    expires_at: int


def create_access_token(
    user_id: UUID, phone_number: str, session_id: UUID | None = None
) -> tuple[str, int]:
    """Sign a 30-day access token. Returns (token, expires_in_seconds)."""
    expires_in = settings.jwt_expire_minutes * 60
    issued_at = now_utc()

    claims: dict[str, Any] = {
        "sub": str(user_id),
        "user_id": str(user_id),
        "phone_number": phone_number,
        "session_id": str(session_id) if session_id else None,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=expires_in)).timestamp()),
        "iss": ISSUER,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            # A fixed algorithm list is what stops an attacker swapping in
            # `none` or an asymmetric alg (CVE-2024-33663).
            algorithms=[settings.jwt_algorithm],
            issuer=ISSUER,
        )
        session_id = claims.get("session_id")
        return TokenPayload(
            user_id=UUID(claims["user_id"]),
            phone_number=claims.get("phone_number", ""),
            session_id=UUID(session_id) if session_id else None,
            issued_at=int(claims.get("iat", 0)),
            expires_at=int(claims.get("exp", 0)),
        )
    except (JWTError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers=_UNAUTHORIZED,
        ) from exc


# --- dependencies -----------------------------------------------------------


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    """Require a valid bearer token. Use on any protected route."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers=_UNAUTHORIZED,
        )
    return decode_access_token(credentials.credentials)


async def get_optional_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload | None:
    """Most pilgrim-facing reads stay open — safety information is not gated."""
    if credentials is None:
        return None
    try:
        return decode_access_token(credentials.credentials)
    except HTTPException:
        return None


async def get_current_user(
    payload: Annotated[TokenPayload, Depends(get_token_payload)],
    db: DbSession,
) -> User:
    """Load the signed-in pilgrim, rejecting tokens whose user is gone."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user store unavailable; please retry shortly",
        )

    user = (await db.execute(select(User).where(User.id == payload.user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists",
            headers=_UNAUTHORIZED,
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentToken = Annotated[TokenPayload, Depends(get_token_payload)]
OptionalToken = Annotated[TokenPayload | None, Depends(get_optional_token)]
