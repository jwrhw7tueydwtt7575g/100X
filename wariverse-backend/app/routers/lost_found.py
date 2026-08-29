"""Lost & found reports — mostly separated families, so speed matters."""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.deps import DbSession, Language, get_session_service
from app.models.db_models import LOST_FOUND_SEQUENCE, LostFoundReport
from app.models.schemas import LostFoundCreate, LostFoundResponse
from app.redis_client import get_redis
from app.services.session_service import SessionService
from app.utils import now_ist

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/lost-found", tags=["lost-found"])

# The admin dashboard subscribes to this for a live queue of new reports.
LOST_FOUND_CHANNEL = "wv:lost_found:reports"

# Stored status → the label the pilgrim reads.
STATUS_LABELS = {
    "OPEN": "lost_found_status_open",
    "IN_PROGRESS": "lost_found_status_in_progress",
    "MATCHED": "lost_found_status_matched",
    "RESOLVED": "lost_found_status_resolved",
    "CLOSED": "lost_found_status_closed",
}


async def allocate_reference_id(db: AsyncSession) -> str:
    """Next human-readable reference, e.g. `WF-2026-00124`.

    Drawn from a Postgres sequence rather than a random string so two desks
    filing at the same moment can never mint the same id, and so a pilgrim
    reading it back over a phone gets a short, unambiguous number.

    The counter is monotonic across years by design — resetting it on 1 January
    would need a lock to stay collision-free, and the year already disambiguates.
    """
    from sqlalchemy import text

    number = (await db.execute(text(f"SELECT nextval('{LOST_FOUND_SEQUENCE}')"))).scalar_one()
    return f"WF-{now_ist():%Y}-{number:05d}"


@router.post(
    "",
    response_model=LostFoundResponse,
    status_code=status.HTTP_201_CREATED,
    summary="File a lost person or lost item report",
    responses={503: {"description": "Report store unavailable"}},
)
async def create_report(
    payload: LostFoundCreate,
    db: DbSession,
    language: Language,
    sessions: Annotated[SessionService, Depends(get_session_service)],
) -> LostFoundResponse:
    if db is None:
        # Losing a report is worse than an error the client can retry with.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="report store unavailable; please call the helpline "
            f"{settings.wari_control_room}",
        )

    lang = payload.language or language

    # The client sends its opaque session string; resolve it to the real row so
    # a volunteer can read the conversation this report came out of.
    session_uuid = None
    if payload.session_id:
        state = await sessions.resolve(payload.session_id)
        if await sessions.ensure_row(state):
            session_uuid = state.session_id

    report = LostFoundReport(
        reference_id=await allocate_reference_id(db),
        incident_type=payload.incident_type,
        description=payload.description,
        reporter_phone=payload.reporter_phone,
        last_seen_location=payload.last_seen_location,
        latitude=payload.latitude,
        longitude=payload.longitude,
        session_id=session_uuid,
        status="OPEN",
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    log.warning(
        "lost_found_created",
        reference_id=report.reference_id,
        incident_type=report.incident_type,
        last_seen_location=report.last_seen_location,
        latitude=report.latitude,
        longitude=report.longitude,
    )
    await _notify_dashboard(report)
    return _to_response(report, lang)


@router.get(
    "/{reference_id}",
    response_model=LostFoundResponse,
    summary="Current status of a report",
    responses={404: {"description": "Unknown reference id"}},
)
async def get_report(
    db: DbSession,
    language: Language,
    reference_id: Annotated[str, Path(max_length=20, examples=["WF-2026-00124"])],
) -> LostFoundResponse:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="report store unavailable"
        )

    report = (
        await db.execute(
            select(LostFoundReport).where(
                LostFoundReport.reference_id == reference_id.strip().upper()
            )
        )
    ).scalar_one_or_none()

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown reference id: {reference_id}",
        )

    return _to_response(report, language)


# --- helpers ----------------------------------------------------------------


def _to_response(report: LostFoundReport, language: str) -> LostFoundResponse:
    from app.models.schemas import GeoPoint

    loc = None
    if report.latitude is not None and report.longitude is not None:
        loc = GeoPoint(lat=report.latitude, lon=report.longitude)

    return LostFoundResponse(
        reference_id=report.reference_id,
        status=t(STATUS_LABELS.get(report.status, "lost_found_status_open"), language),
        next_action=t("lost_found_next_action", language),
        message=t("lost_found_filed", language),
        incident_type=report.incident_type,  # type: ignore[arg-type]
        description=report.description,
        last_seen_location=report.last_seen_location,
        location=loc,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


async def _notify_dashboard(report: LostFoundReport) -> None:
    """Publish to the control-room dashboard; best effort by design.

    The report is already committed, so a failed publish costs the dashboard a
    live update, not the report itself.
    """
    client = get_redis()
    if client is None:
        return
    try:
        data: dict[str, str | float | None] = {
            "reference_id": report.reference_id,
            "incident_type": report.incident_type,
            "description": report.description,
            "last_seen_location": report.last_seen_location,
            "reporter_phone": report.reporter_phone,
            "created_at": report.created_at.isoformat(),
        }
        if report.latitude is not None and report.longitude is not None:
            data["location"] = {"latitude": report.latitude, "longitude": report.longitude}

        await client.publish(
            LOST_FOUND_CHANNEL,
            json.dumps(
                data,
                ensure_ascii=False,
            ),
        )
    except (RedisError, OSError) as exc:
        log.warning(
            "lost_found_publish_failed",
            reference_id=report.reference_id,
            error=str(exc),
        )
