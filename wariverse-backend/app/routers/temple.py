"""Temple information: darshan types, aarti schedule, rules, live queue."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import or_, select

from app.data.temple import (
    TEMPLE_CONTACT,
    TEMPLE_ID,
    TEMPLE_LAT,
    TEMPLE_LON,
    temple_content,
)
from app.deps import DbSession, Language, get_crowd_service
from app.models.db_models import TempleNotice
from app.models.schemas import GeoPoint, ScheduleEntry, TempleInfoResponse
from app.services.crowd_service import CrowdService, ZoneNotFoundError
from app.utils import now_utc

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/temple", tags=["temple"])


@router.get(
    "/info",
    response_model=TempleInfoResponse,
    summary="Shri Vitthal Rukmini Mandir information with live queue status",
)
async def get_temple_info(
    db: DbSession,
    crowd: Annotated[CrowdService, Depends(get_crowd_service)],
    language: Language,
) -> TempleInfoResponse:
    content = temple_content(language)

    try:
        queue = await crowd.read_zone("darshan_queue")
        queue_status, wait_minutes = queue.density_level, queue.wait_minutes
    except ZoneNotFoundError:
        queue_status, wait_minutes = "moderate", None

    return TempleInfoResponse(
        temple_id=TEMPLE_ID,
        name=content["name"],
        deity=content["deity"],
        location=GeoPoint(lat=TEMPLE_LAT, lon=TEMPLE_LON),
        address=content["address"],
        language=language,  # type: ignore[arg-type]
        darshan_types=[ScheduleEntry(**entry) for entry in content["darshan_types"]],
        aarti_schedule=[ScheduleEntry(**entry) for entry in content["aarti_schedule"]],
        queue_status=queue_status,  # type: ignore[arg-type]
        live_wait_minutes=wait_minutes,
        dress_code=content["dress_code"],
        rules=content["rules"],
        facilities_on_site=content["facilities_on_site"],
        contact_phone=TEMPLE_CONTACT,
        notices=await _active_notices(db, language),
        updated_at=now_utc(),
    )


async def _active_notices(db, language: str) -> list[str]:
    """Time-bound announcements published by the Mandir Samiti."""
    if db is None:
        return []
    now = now_utc()
    try:
        rows = (
            (
                await db.execute(
                    select(TempleNotice)
                    .where(
                        TempleNotice.is_active.is_(True),
                        TempleNotice.language == language,
                        or_(TempleNotice.active_from.is_(None), TempleNotice.active_from <= now),
                        or_(TempleNotice.active_until.is_(None), TempleNotice.active_until >= now),
                    )
                    .order_by(TempleNotice.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 — notices are additive, never blocking
        log.warning("temple_notices_read_failed", error=str(exc))
        return []

    return [f"{row.title}: {row.body}" for row in rows]
