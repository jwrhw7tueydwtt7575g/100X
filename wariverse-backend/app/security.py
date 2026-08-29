"""JWT issuing/verification and the bearer-token dependencies."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.utils import now_utc

log = structlog.get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


class TokenPayload:
    __slots__ = ("user_id", "phone", "issued_at")

    def __init__(self, user_id: UUID, phone: str, issued_at: int) -> None:
        self.user_id = user_id
        self.phone = phone
        self.issued_at = issued_at


def create_access_token(user_id: UUID, phone: str) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    expires_in = settings.jwt_expire_minutes * 60
    issued_at = now_utc()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "phone": phone,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=expires_in)).timestamp()),
        "iss": "wariverse",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="wariverse",
        )
        return TokenPayload(
            user_id=UUID(payload["sub"]),
            phone=payload.get("phone", ""),
            issued_at=int(payload.get("iat", 0)),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload | None:
    """Most pilgrim-facing reads stay open — safety information is not gated."""
    if credentials is None:
        return None
    try:
        return decode_access_token(credentials.credentials)
    except HTTPException:
        return None
