"""Community seva offerings — free food, stay, water and aid from locals.

These live beside the official facility directory rather than inside it: the
directory is surveyed data the district administration owns, and a langar
someone opens for three days is not the same kind of fact. Search merges the
two and flags which is which, so a pilgrim can tell.

An offering is only shown while it is `is_active` **and** inside its
availability window. A stale pin that sends someone walking to a kitchen that
closed yesterday is worse than no pin.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import CommunityService
from app.services.geo import bounding_box, haversine_m
from app.utils import now_utc

log = structlog.get_logger(__name__)

TOKEN_BYTES = 32


class NotFoundError(LookupError):
    """No active offering with that id."""


class NotOwnerError(PermissionError):
    """The caller cannot manage this offering."""


def new_manage_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_open_now(service: CommunityService, at: datetime | None = None) -> bool:
    moment = at or now_utc()
    return bool(
        service.is_active
        and service.available_from <= moment <= service.available_until
    )


class CommunityServiceRepo:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    # --- writes ------------------------------------------------------------

    async def create(self, payload, user_id=None) -> tuple[CommunityService, str]:
        """Publish an offering. Returns the row and its one-time manage token."""
        if self.db is None:
            raise RuntimeError("community service store unavailable")

        token = new_manage_token()
        service = CommunityService(
            provider_name=payload.provider_name,
            category=payload.category,
            title=payload.title,
            address=payload.address,
            latitude=payload.latitude,
            longitude=payload.longitude,
            available_from=payload.available_from,
            available_until=payload.available_until,
            contact_phone=payload.contact_phone,
            user_id=user_id,
            owner_token_hash=hash_token(token),
            is_active=True,
        )
        self.db.add(service)
        await self.db.commit()
        await self.db.refresh(service)

        log.info(
            "community_service_published",
            service_id=service.id,
            category=service.category,
            provider=service.provider_name,
            authenticated=user_id is not None,
        )
        return service, token

    async def deactivate(
        self, service_id: str, token: str | None, user_id=None, is_admin: bool = False
    ) -> CommunityService:
        """Take an offering down.

        Soft delete: the row is kept so a provider who removes a listing by
        mistake can be helped, and so the control room retains a record of what
        was offered where. It disappears from search and the map immediately.
        """
        if self.db is None:
            raise RuntimeError("community service store unavailable")

        service = (
            await self.db.execute(
                select(CommunityService).where(CommunityService.id == service_id)
            )
        ).scalar_one_or_none()
        if service is None or not service.is_active:
            raise NotFoundError(service_id)

        if not self._may_manage(service, token, user_id, is_admin):
            log.warning(
                "community_service_delete_refused",
                service_id=service_id,
                had_token=bool(token),
                authenticated=user_id is not None,
            )
            raise NotOwnerError(service_id)

        service.is_active = False
        await self.db.commit()
        await self.db.refresh(service)

        log.info("community_service_withdrawn", service_id=service_id)
        return service

    @staticmethod
    def _may_manage(
        service: CommunityService, token: str | None, user_id, is_admin: bool
    ) -> bool:
        if is_admin:
            return True
        if user_id is not None and service.user_id == user_id:
            return True
        if token:
            # compare_digest so a wrong token cannot be narrowed by timing.
            return hmac.compare_digest(service.owner_token_hash, hash_token(token))
        return False

    # --- reads -------------------------------------------------------------

    async def active(
        self,
        lat: float | None = None,
        lon: float | None = None,
        radius_m: int | None = None,
        categories: list[str] | None = None,
        limit: int = 100,
        at: datetime | None = None,
    ) -> list[CommunityService]:
        """Offerings that are live right now, nearest first when located."""
        if self.db is None:
            return []

        moment = at or now_utc()
        stmt = select(CommunityService).where(
            CommunityService.is_active.is_(True),
            CommunityService.available_from <= moment,
            CommunityService.available_until >= moment,
        )
        if categories:
            stmt = stmt.where(CommunityService.category.in_(categories))
        if lat is not None and lon is not None and radius_m:
            min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_m)
            stmt = stmt.where(
                and_(
                    CommunityService.latitude.between(min_lat, max_lat),
                    CommunityService.longitude.between(min_lon, max_lon),
                )
            )

        try:
            rows = (await self.db.execute(stmt.limit(500))).scalars().all()
        except Exception as exc:  # noqa: BLE001 — seva is additive; never break search
            log.warning("community_service_read_failed", error=str(exc))
            return []

        if lat is None or lon is None:
            return list(rows)[:limit]

        within = [
            row
            for row in rows
            if not radius_m
            or haversine_m(lat, lon, row.latitude, row.longitude) <= radius_m
        ]
        within.sort(key=lambda row: haversine_m(lat, lon, row.latitude, row.longitude))
        return within[:limit]

    async def owned_by(
        self, user_id=None, tokens: list[str] | None = None, limit: int = 50
    ) -> list[CommunityService]:
        """A provider's own listings, for the Settings page.

        Matched by account or by any of the manage tokens the device holds —
        an anonymous provider still needs to find what they published.
        """
        if self.db is None or (user_id is None and not tokens):
            return []

        clauses = []
        if user_id is not None:
            clauses.append(CommunityService.user_id == user_id)
        if tokens:
            clauses.append(
                CommunityService.owner_token_hash.in_([hash_token(t) for t in tokens])
            )

        try:
            rows = (
                await self.db.execute(
                    select(CommunityService)
                    .where(or_(*clauses))
                    .order_by(CommunityService.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        except Exception as exc:  # noqa: BLE001
            log.warning("community_service_owned_read_failed", error=str(exc))
            return []
        return list(rows)
