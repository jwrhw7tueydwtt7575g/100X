"""Emergency dispatch.

Shared by `POST /api/sos/trigger` and by the confirmation step of the chat flow
so both paths create identical records and identical wording.

`sos_events.session_id` is NOT NULL, so a panic press that arrives without a
session gets an anonymous one created for it — an unregistered pilgrim must
still be able to call for help.

An SOS is acknowledged even if Postgres is unavailable: the pilgrim always gets
the helpline numbers back, and the failure is logged at error level for the
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
from app.services.session_service import SessionService
from app.utils import now_utc

log = structlog.get_logger(__name__)

SOS_CHANNEL = "wv:sos:events"

# Responders move by two-wheeler/ambulance through crowds: slower than road
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
        self.sessions = SessionService(db)

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

        session_id = await self._resolve_session(session_id, user_id, language)

        event = SosEvent(
            id=uuid4(),
            session_id=session_id,
            user_id=user_id,
            latitude=lat,
            longitude=lon,
            status="ACTIVATED",
            notes=self._notes(emergency_type, desk, eta, phone, description, accuracy_m),
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
            session_id=str(session_id),
            emergency_type=emergency_type,
            lat=lat,
            lon=lon,
            dispatched_to=desk,
            eta_minutes=eta,
            user_id=str(user_id) if user_id else None,
        )
        await self._publish(event, emergency_type, eta)

        return SosEventResponse(
            sos_id=event.id,
            session_id=str(session_id),
            status="ACTIVATED",
            emergency_type=emergency_type,  # type: ignore[arg-type]
            location=GeoPoint(lat=lat, lon=lon, accuracy_m=accuracy_m),
            dispatched_to=desk,
            eta_minutes=eta,
            nearest_facility=nearest,
            helpline_numbers=self.helplines(),
            message=t(
                "sos_dispatched",
                language,
                desk=desk,
                eta=eta,
                helpline=settings.emergency_helpline,
            ),
            language=language,  # type: ignore[arg-type]
            created_at=created_at,
        )

    @staticmethod
    def helplines() -> list[str]:
        # 112 — national emergency, 108 — ambulance, plus the Wari control room.
        return [settings.emergency_helpline, "108", settings.wari_control_room]

    # --- internals ---------------------------------------------------------

    async def _resolve_session(
        self, session_id: UUID | None, user_id: UUID | None, language: str
    ) -> UUID:
        """Return a session id that satisfies the NOT NULL foreign key.

        Creating one for an anonymous panic press keeps the row valid without
        making registration a precondition for asking for help.
        """
        state = await self.sessions.get_or_create(
            session_id=session_id, user_id=user_id, language=language
        )
        await self.sessions.ensure_row(state)
        return state.session_id

    @staticmethod
    def _notes(
        emergency_type: str,
        desk: str,
        eta: int,
        phone: str | None,
        description: str | None,
        accuracy_m: float | None,
    ) -> str:
        """Dispatch detail the control room reads back.

        The spec gives `sos_events` a single free-text `notes` column, so the
        operational fields that used to be columns are folded in here as
        `key=value` lines rather than dropped.
        """
        parts = [
            f"type={emergency_type}",
            f"dispatched_to={desk}",
            f"eta_minutes={eta}",
        ]
        if phone:
            parts.append(f"callback={phone}")
        if accuracy_m is not None:
            parts.append(f"gps_accuracy_m={round(accuracy_m)}")
        if description:
            parts.append(f"description={description}")
        return "\n".join(parts)

    @staticmethod
    def _eta_minutes(nearest: FacilityOut | None) -> int:
        if nearest is None:
            return 15
        minutes = (nearest.distance_m / 1000) / _RESPONDER_KMPH * 60
        return max(_MIN_ETA_MINUTES, round(minutes))

    async def _publish(self, event: SosEvent, emergency_type: str, eta: int) -> None:
        """Fan out to the control-room dashboard; best effort by design."""
        client = get_redis()
        if client is None:
            return
        try:
            await client.publish(
                SOS_CHANNEL,
                (
                    f'{{"sos_id":"{event.id}","emergency_type":"{emergency_type}",'
                    f'"latitude":{event.latitude},"longitude":{event.longitude},'
                    f'"eta_minutes":{eta}}}'
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("sos_publish_failed", sos_id=str(event.id), error=str(exc))
