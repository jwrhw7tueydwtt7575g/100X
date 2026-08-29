"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.services.crowd_service import CrowdService
from app.services.facility_service import FacilityService
from app.services.llm_orchestrator import LLMOrchestrator
from app.services.route_service import RouteService
from app.services.session_service import SessionService
from app.services.sos_service import SosService

DbSession = Annotated[AsyncSession | None, Depends(get_db)]


async def language_param(
    language: str | None = Query(
        default=None,
        description="Response language: mr, hi, en, kn or te. Defaults to DEFAULT_LANGUAGE.",
        examples=["mr"],
    ),
) -> str:
    if language and language in settings.supported_languages:
        return language
    return settings.default_language


Language = Annotated[str, Depends(language_param)]


def get_crowd_service(db: DbSession) -> CrowdService:
    return CrowdService(db)


def get_facility_service(db: DbSession) -> FacilityService:
    return FacilityService(db)


def get_route_service(db: DbSession) -> RouteService:
    return RouteService(db)


def get_session_service(db: DbSession) -> SessionService:
    return SessionService(db)


def get_sos_service(db: DbSession) -> SosService:
    return SosService(db)


def get_orchestrator(db: DbSession) -> LLMOrchestrator:
    return LLMOrchestrator(db)
