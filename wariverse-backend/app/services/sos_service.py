"""Emergency dispatch.

Shared by `POST /api/sos/trigger` and by the confirmation step of the chat flow
so both paths create identical records and identical wording. An SOS is
recorded and acknowledged even if Postgres is unavailable — the pilgrim always
gets the helpline numbers back, and the failure is logged loudly for the
control room to reconcile.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.models.db_models import SosEvent
from app.models.schemas import FacilityOut, GeoPoint, SosEventResponse
from app.redis_client import get_redis
from app.services.facility_service import FacilityService
from app.utils import now_utc

log = structlog.get_logger(__name__)

SOS_CHANNEL = "wv:sos:events"

# Responders move by two-wheeler/ambulance through crowds; slower than road
# speed, much faster than a walking pilgrim.
_RESPONDER_KMPH = 12.0
_MIN_ETA_MINUTES = 3

_RESPONDER_TYPES: dict[str, list[str]] = {
    "medical": ["medical", "police"],
    "crowd_crush": ["police", "medical"],
    "fire": ["police", "medical"],
    "harassment": ["police"],
    "lost_person": ["lost_found_desk", "police"],
    "other": ["police", "medical"],
}


class SosService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self.facilities = FacilityService(db)

    async def dispatch(
        self,
        *,
        lat: float,
        lon: float,
        emergency_type: str = "other",
        language: str = "mr",
        user_id: UUID | None = None,
        session_id: UUID | None = None,
        phone: str | None = None,
        description: str | None = None,
        accuracy_m: float | None = None,
    ) -> SosEventResponse:
        nearest = await self.facilities.nearest(
            lat,
            lon,
            _RESPONDER_TYPES.get(emergency_type, _RESPONDER_TYPES["other"]),
            radius_m=8000,
            language=language,
        )
        eta = self._eta_minutes(nearest)
        desk = nearest.name if nearest else settings.wari_control_room

        event = SosEvent(
            id=uuid4(),
            user_id=user_id,
            session_id=session_id,
            phone=phone,
            emergency_type=emergency_type,
            status="dispatched",
            lat=lat,
            lon=lon,
            accuracy_m=accuracy_m,
            description=description,
            language=language,
            nearest_facility_id=nearest.id if nearest else None,
            dispatched_to=desk,
            eta_minutes=eta,
        )
        created_at = now_utc()

        if self.db is not None:
            try:
                self.db.add(event)
                await self.db.commit()
                await self.db.refresh(event)
                created_at = event.created_at
            except Exception as exc:  # noqa: BLE001 — never drop an SOS on a DB error
                await self.db.rollback()
                log.error(
                    "sos_persist_failed",
                    sos_id=str(event.id),
                    lat=lat,
                    lon=lon,
                    emergency_type=emergency_type,
                    error=str(exc),
                )

        log.warning(
            "sos_dispatched",
            sos_id=str(event.id),
            emergency_type=emergency_type,
            lat=lat,
            lon=lon,
            dispatched_to=desk,
            eta_minutes=eta,
            user_id=str(user_id) if user_id else None,
        )
        await self._publish(event)

        return SosEventResponse(
            sos_id=event.id,
            status="dispatched",
            emergency_type=emergency_type,  # type: ignore[arg-type]
            location=GeoPoint(lat=lat, lon=lon, accuracy_m=accuracy_m),
            dispatched_to=desk,
            eta_minutes=eta,
            nearest_facility=nearest,
            helpline_numbers=self.helplines(),
            message=t("sos_dispatched", language, desk=desk, eta=eta,
                      helpline=settings.emergency_helpline),
            language=language,  # type: ignore[arg-type]
            created_at=created_at,
        )

    @staticmethod
    def helplines() -> list[str]:
        # 112 — national emergency, 108 — ambulance, plus the Wari control room.
        return [settings.emergency_helpline, "108", settings.wari_control_room]

    @staticmethod
    def _eta_minutes(nearest: FacilityOut | None) -> int:
        if nearest is None:
            return 15
        minutes = (nearest.distance_m / 1000) / _RESPONDER_KMPH * 60
        return max(_MIN_ETA_MINUTES, round(minutes))

    async def _publish(self, event: SosEvent) -> None:
        """Fan out to the control-room dashboard; best effort by design."""
        client = get_redis()
        if client is None:
            return
        try:
            await client.publish(
                SOS_CHANNEL,
                (
                    f'{{"sos_id":"{event.id}","emergency_type":"{event.emergency_type}",'
                    f'"lat":{event.lat},"lon":{event.lon},"eta_minutes":{event.eta_minutes}}}'
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("sos_publish_failed", sos_id=str(event.id), error=str(exc))
