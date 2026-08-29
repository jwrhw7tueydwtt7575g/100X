"""Temple information card."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import Language, get_temple_service
from app.models.schemas import TempleInfoResponse
from app.services.temple_service import TempleService

router = APIRouter(prefix="/temple", tags=["temple"])


@router.get(
    "/info",
    response_model=TempleInfoResponse,
    summary="Timings, rituals, events and visitor guidance",
)
async def get_temple_info(
    temple: Annotated[TempleService, Depends(get_temple_service)],
    language: Language,
) -> TempleInfoResponse:
    return await temple.get(language)
