"""Community seva offerings, published from the app's Settings page.

Publishing is open to anyone — a resident opening a langar should not need an
account — but **taking a listing down is not**. Creation returns a one-time
`manage_token`; deletion requires it, or the owning account, or the admin key.
Without that check the list endpoint would hand out every id and a single sweep
could erase every free kitchen from the map.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.config import settings
from app.deps import DbSession
from app.models.db_models import CommunityService
from app.models.schemas import (
    CommunityServiceCreate,
    CommunityServiceCreated,
    CommunityServiceList,
    CommunityServiceOut,
    SevaCategory,
)
from app.security import OptionalToken
from app.services.community_service import (
    CommunityServiceRepo,
    NotFoundError,
    NotOwnerError,
    is_open_now,
)
from app.services.geo import haversine_m

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/community/services", tags=["community"])

ManageToken = Annotated[
    str | None,
    Header(
        alias="X-Manage-Token",
        description="The token returned when the offering was published.",
    ),
]


def _to_out(
    service: CommunityService, lat: float | None = None, lon: float | None = None
) -> CommunityServiceOut:
    distance = (
        int(round(haversine_m(lat, lon, service.latitude, service.longitude)))
        if lat is not None and lon is not None
        else None
    )
    return CommunityServiceOut(
        id=service.id,
        provider_name=service.provider_name,
        category=service.category,  # type: ignore[arg-type]
        title=service.title,
        address=service.address,
        latitude=service.latitude,
        longitude=service.longitude,
        available_from=service.available_from,
        available_until=service.available_until,
        contact_phone=service.contact_phone,
        is_active=service.is_active,
        is_open_now=is_open_now(service),
        distance_m=distance,
        is_locked=bool(getattr(service, "is_locked", False) or False),
        locked_by_name=getattr(service, "locked_by_name", None),
        locked_by_phone=getattr(service, "locked_by_phone", None),
        locked_at=getattr(service, "locked_at", None),
        created_at=service.created_at,
    )


def _require_store(db) -> None:
    pass


@router.post(
    "",
    response_model=CommunityServiceCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a free seva offering",
    responses={503: {"description": "Store unavailable"}},
)
async def publish(
    payload: CommunityServiceCreate,
    request: Request,
    db: DbSession,
    caller: OptionalToken = None,
) -> CommunityServiceCreated:
    _require_store(db)

    service, token = await CommunityServiceRepo(db).create(
        payload, user_id=caller.user_id if caller else None
    )
    log.info(
        "community_service_created",
        service_id=service.id,
        category=service.category,
        request_id=getattr(request.state, "request_id", None),
    )
    return CommunityServiceCreated(
        **_to_out(service).model_dump(), manage_token=token
    )


@router.get(
    "",
    response_model=CommunityServiceList,
    summary="Live seva offerings, for the map and the Settings list",
)
async def list_services(
    db: DbSession,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[int | None, Query(ge=50, le=100_000)] = None,
    category: Annotated[list[SevaCategory] | None, Query()] = None,
    mine: Annotated[
        bool, Query(description="Only the caller's own listings.")
    ] = False,
    manage_token: ManageToken = None,
    caller: OptionalToken = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> CommunityServiceList:
    """Active offerings, or — with `mine=true` — the caller's own.

    `mine` matches on the account **or** a manage token, so an anonymous
    provider can still find and withdraw what they published.
    """
    if db is None:
        # Seva is additive; an empty list is better than an error on a map screen.
        return CommunityServiceList(services=[])

    repo = CommunityServiceRepo(db)
    if mine:
        rows = await repo.owned_by(
            user_id=caller.user_id if caller else None,
            tokens=[manage_token] if manage_token else None,
            limit=limit,
        )
    else:
        rows = await repo.active(
            lat=lat,
            lon=lng,
            radius_m=radius_m,
            categories=list(category) if category else None,
            limit=limit,
        )
    return CommunityServiceList(services=[_to_out(row, lat, lng) for row in rows])


@router.delete(
    "/{service_id}",
    response_model=CommunityServiceOut,
    summary="Withdraw an offering",
    responses={
        403: {"description": "Not the provider of this offering"},
        404: {"description": "Unknown or already withdrawn"},
        503: {"description": "Store unavailable"},
    },
)
async def withdraw(
    service_id: str,
    request: Request,
    db: DbSession,
    manage_token: ManageToken = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    caller: OptionalToken = None,
) -> CommunityServiceOut:
    _require_store(db)

    is_admin = bool(
        settings.admin_api_key and x_api_key and x_api_key == settings.admin_api_key
    )
    try:
        service = await CommunityServiceRepo(db).deactivate(
            service_id,
            token=manage_token,
            user_id=caller.user_id if caller else None,
            is_admin=is_admin,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown or already withdrawn: {service_id}",
        ) from exc
    except NotOwnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the provider can withdraw this offering",
        ) from exc

    log.info(
        "community_service_deleted",
        service_id=service_id,
        by_admin=is_admin,
        request_id=getattr(request.state, "request_id", None),
    )
    return _to_out(service)


@router.post(
    "/{service_id}/lock",
    response_model=CommunityServiceOut,
    summary="Lock/reserve a community service offering for a pilgrim",
)
async def lock_service(
    service_id: str,
    db: DbSession,
    caller: OptionalToken = None,
    name: Annotated[str | None, Query()] = None,
    phone: Annotated[str | None, Query()] = None,
) -> CommunityServiceOut:
    try:
        service = await CommunityServiceRepo(db).lock(
            service_id=service_id,
            user_id=caller.user_id if caller else None,
            locked_by_name=name,
            locked_by_phone=phone,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown offering: {service_id}",
        ) from exc
    return _to_out(service)


@router.post(
    "/{service_id}/unlock",
    response_model=CommunityServiceOut,
    summary="Release lock on a community service offering",
)
async def unlock_service(
    service_id: str,
    db: DbSession,
) -> CommunityServiceOut:
    try:
        service = await CommunityServiceRepo(db).unlock(service_id=service_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown offering: {service_id}",
        ) from exc
    return _to_out(service)
