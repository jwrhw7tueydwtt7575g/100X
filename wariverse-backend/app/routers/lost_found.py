"""Lost & found reports — mostly separated families, so speed matters."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.data.i18n import t
from app.deps import DbSession, Language
from app.models.db_models import LostFoundReport
from app.models.schemas import LostFoundCreate, LostFoundResponse
from app.utils import generate_ref_id

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/lost-found", tags=["lost-found"])

_MAX_REF_ATTEMPTS = 5


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
) -> LostFoundResponse:
    if db is None:
        # Losing a report is worse than an error the client can retry with.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="report store unavailable; please call the helpline "
            f"{settings.wari_control_room}",
        )

    lang = payload.language or language
    report: LostFoundReport | None = None

    for _ in range(_MAX_REF_ATTEMPTS):
        candidate = LostFoundReport(
            ref_id=generate_ref_id("WV"),
            report_type=payload.report_type,
            status="open",
            subject_name=payload.subject_name,
            subject_age=payload.subject_age,
            description=payload.description,
            distinguishing_marks=payload.distinguishing_marks,
            last_seen_location=payload.last_seen_location,
            last_seen_lat=payload.last_seen_lat,
            last_seen_lon=payload.last_seen_lon,
            last_seen_at=payload.last_seen_at,
            reporter_name=payload.reporter_name,
            contact_phone=payload.contact_phone,
            photo_url=payload.photo_url,
            language=lang,
            assigned_desk="Wari Control Room",
        )
        db.add(candidate)
        try:
            await db.commit()
        except IntegrityError:
            # Reference ids are short enough to collide occasionally; retry.
            await db.rollback()
            continue
        await db.refresh(candidate)
        report = candidate
        break

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="could not allocate a reference id, please retry",
        )

    log.info(
        "lost_found_created",
        ref_id=report.ref_id,
        report_type=report.report_type,
        has_photo=bool(report.photo_url),
    )
    return _to_response(report, lang)


@router.get(
    "/{ref_id}",
    response_model=LostFoundResponse,
    summary="Look up a report by its reference id",
    responses={404: {"description": "Unknown reference id"}},
)
async def get_report(
    db: DbSession,
    language: Language,
    ref_id: Annotated[str, Path(max_length=16, examples=["WV-7KQ4XM"])],
) -> LostFoundResponse:
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="report store unavailable"
        )

    report = (
        await db.execute(
            select(LostFoundReport).where(LostFoundReport.ref_id == ref_id.strip().upper())
        )
    ).scalar_one_or_none()

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown reference id: {ref_id}"
        )

    return _to_response(report, report.language or language)


def _to_response(report: LostFoundReport, language: str) -> LostFoundResponse:
    return LostFoundResponse(
        ref_id=report.ref_id,
        report_type=report.report_type,  # type: ignore[arg-type]
        status=report.status,  # type: ignore[arg-type]
        subject_name=report.subject_name,
        subject_age=report.subject_age,
        description=report.description,
        distinguishing_marks=report.distinguishing_marks,
        last_seen_location=report.last_seen_location,
        last_seen_at=report.last_seen_at,
        reporter_name=report.reporter_name,
        contact_phone=report.contact_phone,
        photo_url=report.photo_url,
        language=language,  # type: ignore[arg-type]
        assigned_desk=report.assigned_desk,
        resolution_note=report.resolution_note,
        helpline=settings.wari_control_room,
        message=t(
            "lost_found_created",
            language,
            ref_id=report.ref_id,
            helpline=settings.wari_control_room,
        ),
        created_at=report.created_at,
        updated_at=report.updated_at,
    )
