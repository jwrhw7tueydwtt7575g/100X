"""Temple information: Postgres record, Redis cache, seeded default.

Read path: Redis (`temple:info:{language}`, 1 hour TTL) → the `temple_info` row
→ the bundled default. The card is editable by an operator through
`PUT /api/admin/temple/info`, which writes the row and drops the cache so a
correction during the Wari is visible immediately rather than an hour later.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.temple import DEFAULT_TEMPLE_INFO, temple_defaults
from app.models.db_models import TempleInfo
from app.models.schemas import TempleInfoResponse
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

CACHE_PREFIX = "temple:info:"
CACHE_TTL_SECONDS = 3600  # 1 hour


class TempleService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def get(self, language: str = "en") -> TempleInfoResponse:
        if cached := await self._read_cache(language):
            return cached

        row = await self._read_row(language)
        info = (
            TempleInfoResponse(
                title=row.title,
                timings=row.timings,
                rituals=list(row.rituals or []),
                events=list(row.events or []),
                description=row.description,
            )
            if row is not None
            else TempleInfoResponse(**temple_defaults(language))
        )
        await self._write_cache(language, info)
        return info

    async def update(self, language: str, changes: dict[str, Any]) -> TempleInfoResponse:
        """Apply a partial update, creating the row from defaults if absent."""
        if self.db is None:
            raise RuntimeError("temple info store unavailable")

        row = await self._read_row(language)
        if row is None:
            defaults = temple_defaults(language)
            row = TempleInfo(
                language=language,
                title=defaults["title"],
                timings=defaults["timings"],
                rituals=defaults["rituals"],
                events=defaults["events"],
                description=defaults["description"],
            )
            self.db.add(row)

        for field, value in changes.items():
            if value is not None:
                setattr(row, field, value)

        await self.db.commit()
        await self.db.refresh(row)
        await self._clear_cache(language)

        log.warning(
            "temple_info_updated", language=language, fields=sorted(changes)
        )
        return await self.get(language)

    async def seed_defaults(self) -> int:
        """Insert the bundled content for any language with no row yet."""
        if self.db is None:
            return 0

        created = 0
        for language, defaults in DEFAULT_TEMPLE_INFO.items():
            if await self._read_row(language) is not None:
                continue
            self.db.add(TempleInfo(language=language, **defaults))
            created += 1
        if created:
            await self.db.commit()
        return created

    # --- storage -----------------------------------------------------------

    async def _read_row(self, language: str) -> TempleInfo | None:
        if self.db is None:
            return None
        try:
            return (
                await self.db.execute(
                    select(TempleInfo).where(TempleInfo.language == language)
                )
            ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 — the default still serves
            log.warning("temple_info_read_failed", error=str(exc))
            return None

    async def _read_cache(self, language: str) -> TempleInfoResponse | None:
        client = get_redis()
        if client is None:
            return None
        try:
            raw = await client.get(f"{CACHE_PREFIX}{language}")
            return TempleInfoResponse(**json.loads(raw)) if raw else None
        except (RedisError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("temple_cache_read_failed", error=str(exc))
            return None

    async def _write_cache(self, language: str, info: TempleInfoResponse) -> None:
        client = get_redis()
        if client is None:
            return
        try:
            await client.set(
                f"{CACHE_PREFIX}{language}",
                json.dumps(info.model_dump(), ensure_ascii=False),
                ex=CACHE_TTL_SECONDS,
            )
        except (RedisError, OSError) as exc:
            log.warning("temple_cache_write_failed", error=str(exc))

    async def _clear_cache(self, language: str) -> None:
        client = get_redis()
        if client is None:
            return
        try:
            await client.delete(f"{CACHE_PREFIX}{language}")
        except (RedisError, OSError) as exc:
            log.warning("temple_cache_clear_failed", error=str(exc))
