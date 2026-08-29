"""Emergency dispatch. Safety-critical — read the ordering notes before editing.

`trigger()` runs a fixed sequence:

1. **Persist as PENDING and commit.** The durable record exists before anyone
   is told about it, so a crash after this point still leaves an emergency the
   control room can find.
2. **Publish to the `sos:new` Redis channel** so the dashboard lights up now
   rather than on its next poll.
3. **Mark ACTIVATED** and return.
4. **SMS the control room** (best effort, if configured).

Two deliberate choices:

* **A failed publish still activates.** Pub/sub is a live-push optimisation;
  `GET /api/admin/sos/active` reads Postgres, so the dashboard sees the event
  either way. Leaving it PENDING would make a delivered emergency look
  undispatched.
* **A failed database write does not fail the request.** The pilgrim still gets
  the helpline numbers and the event is logged at error level for manual
  reconciliation. Returning a 500 to someone whose relative has collapsed helps
  nobody.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid4

import structlog
from redis.exceptions import RedisError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.data.i18n import t
from app.models.db_models import SosEvent
from app.models.schemas import (
    FacilityOut,
    SosEventAdmin,
    SosTriggerResponse,
)
from app.redis_client import get_redis
from app.services.facility_service import FacilityService
from app.services.session_service import SessionService
from app.utils import format_clock, now_utc

log = structlog.get_logger(__name__)

# The control-room dashboard subscribes to this.
SOS_CHANNEL = "sos:new"

# Statuses that still need someone to act.
UNRESOLVED = ("PENDING", "ACTIVATED")

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


class SosNotFoundError(LookupError):
    """Raised when no SOS event has the given id."""


class SosService:
    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self.facilities = FacilityService(db)
        self.sessions = SessionService(db)

    # --- trigger -----------------------------------------------------------

    async def trigger(
        self,
        *,
        latitude: float | None,
        longitude: float | None,
        session_id: str | None = None,
        channel: str = "app",
        emergency_type: str = "other",
        language: str = "en",
        user_id: UUID | None = None,
        phone: str | None = None,
        description: str | None = None,
        accuracy_m: float | None = None,
    ) -> SosTriggerResponse:
        """Raise an emergency.

        Coordinates are optional because a phone caller on the IVR line has no
        GPS. A located emergency gets a nearest post and an ETA; an unlocated
        one still creates the record and reaches the control room, who call the
        number back. Refusing it because there is no fix would be the worst
        possible failure mode.
        """
        located = latitude is not None and longitude is not None

        nearest = (
            await self.facilities.nearest(
                latitude,  # type: ignore[arg-type]
                longitude,  # type: ignore[arg-type]
                _RESPONDER_TYPES.get(emergency_type, _RESPONDER_TYPES["other"]),
                radius_m=8000,
                language=language,
            )
            if located
            else None
        )
        eta = self._eta_minutes(nearest) if located else None
        desk = nearest.name if nearest else settings.wari_control_room

        session_uuid = await self._resolve_session(session_id, user_id, language, channel)

        event = SosEvent(
            id=uuid4(),
            session_id=session_uuid,
            user_id=user_id,
            latitude=latitude,
            longitude=longitude,
            status="PENDING",
            channel=channel,
            notes=self._notes(emergency_type, desk, eta, phone, description, accuracy_m),
        )
        created_at = now_utc()
        persisted = await self._persist_pending(event)
        if persisted:
            created_at = event.created_at

        log.warning(
            "sos_triggered",
            sos_id=str(event.id),
            session_id=session_id,
            internal_session_id=str(session_uuid) if session_uuid else None,
            latitude=latitude,
            longitude=longitude,
            channel=channel,
            emergency_type=emergency_type,
            dispatched_to=desk,
            eta_minutes=eta,
            user_id=str(user_id) if user_id else None,
            persisted=persisted,
            timestamp=created_at.isoformat(),
        )

        announced = await self._publish(event, emergency_type, desk, eta, created_at)
        await self._activate(event, persisted)
        await self._alert_control_room(event, emergency_type, desk, latitude, longitude)

        return SosTriggerResponse(
            sos_id=event.id,
            status="ACTIVATED",
            message=t(
                "sos_activated" if located else "ivr_sos_no_location", language
            ),
            control_room_status=t(
                "control_room_connected" if announced else "control_room_standing_by",
                language,
            ),
            timestamp=format_clock(created_at),
        )

    # --- admin operations --------------------------------------------------

    async def list_active(self, limit: int = 100) -> list[SosEventAdmin]:
        """Unresolved events, newest first."""
        if self.db is None:
            return []

        rows = (
            (
                await self.db.execute(
                    select(SosEvent)
                    .where(SosEvent.status.in_(UNRESOLVED))
                    .order_by(desc(SosEvent.created_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [self._to_admin(row) for row in rows]

    async def set_status(
        self, sos_id: UUID, status: str, note: str | None = None
    ) -> SosEventAdmin:
        if self.db is None:
            raise RuntimeError("sos store unavailable")

        event = (
            await self.db.execute(select(SosEvent).where(SosEvent.id == sos_id))
        ).scalar_one_or_none()
        if event is None:
            raise SosNotFoundError(str(sos_id))

        previous = event.status
        event.status = status
        # Set on the way into RESOLVED, cleared on the way back out, so a
        # reopened emergency does not read as already closed.
        event.resolved_at = now_utc() if status == "RESOLVED" else None
        if note:
            event.notes = f"{event.notes}\n{note}" if event.notes else note

        await self.db.commit()
        await self.db.refresh(event)

        log.warning(
            "sos_status_changed",
            sos_id=str(sos_id),
            previous_status=previous,
            status=status,
            note=note,
        )
        return self._to_admin(event)

    async def resolve(self, sos_id: UUID, note: str | None = None) -> SosEventAdmin:
        return await self.set_status(sos_id, "RESOLVED", note)

    @staticmethod
    def helplines() -> list[str]:
        # 112 — national emergency, 108 — ambulance, plus the Wari control room.
        return [settings.emergency_helpline, "108", settings.wari_control_room]

    # --- internals ---------------------------------------------------------

    async def _persist_pending(self, event: SosEvent) -> bool:
        """Commit the row before anyone is told the emergency exists."""
        if self.db is None:
            log.error(
                "sos_not_persisted",
                sos_id=str(event.id),
                reason="no database session",
                latitude=event.latitude,
                longitude=event.longitude,
            )
            return False
        try:
            self.db.add(event)
            await self.db.commit()
            await self.db.refresh(event)
            return True
        except Exception as exc:  # noqa: BLE001 — never drop an SOS on a DB error
            await self.db.rollback()
            log.error(
                "sos_persist_failed",
                sos_id=str(event.id),
                latitude=event.latitude,
                longitude=event.longitude,
                error=str(exc),
            )
            return False

    async def _activate(self, event: SosEvent, persisted: bool) -> None:
        event.status = "ACTIVATED"
        if not persisted or self.db is None:
            return
        try:
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            # The row survives as PENDING, which the dashboard still lists.
            log.error("sos_activate_failed", sos_id=str(event.id), error=str(exc))

    async def _publish(
        self,
        event: SosEvent,
        emergency_type: str,
        desk: str,
        eta: int | None,
        created_at: datetime,
    ) -> bool:
        """Push the full event to the dashboard. Returns True if it went out."""
        client = get_redis()
        if client is None:
            log.error("sos_publish_unavailable", sos_id=str(event.id))
            return False

        payload = {
            "sos_id": str(event.id),
            "session_id": str(event.session_id) if event.session_id else None,
            "status": "ACTIVATED",
            "latitude": event.latitude,
            "longitude": event.longitude,
            "channel": event.channel,
            "emergency_type": emergency_type,
            "dispatched_to": desk,
            "eta_minutes": eta,
            "notes": event.notes,
            "created_at": created_at.isoformat(),
            "timestamp": format_clock(created_at),
        }
        try:
            await client.publish(SOS_CHANNEL, json.dumps(payload, ensure_ascii=False))
            return True
        except (RedisError, OSError) as exc:
            # Not fatal: the dashboard also polls GET /api/admin/sos/active,
            # which reads Postgres.
            log.error("sos_publish_failed", sos_id=str(event.id), error=str(exc))
            return False

    async def _alert_control_room(
        self,
        event: SosEvent,
        emergency_type: str,
        desk: str,
        latitude: float | None,
        longitude: float | None,
    ) -> None:
        """Text the control room, if a number is configured. Best effort."""
        if not settings.control_room_phone:
            return

        from app.services.sms import send_control_room_alert

        try:
            await send_control_room_alert(
                settings.control_room_phone,
                sos_id=str(event.id),
                emergency_type=emergency_type,
                latitude=latitude,
                longitude=longitude,
                nearest=desk,
            )
        except Exception as exc:  # noqa: BLE001 — an SMS failure must not raise
            log.error("sos_sms_failed", sos_id=str(event.id), error=str(exc))

    async def _resolve_session(
        self, session_id: str | None, user_id: UUID | None, language: str, channel: str
    ) -> UUID | None:
        """Return a session id for the NOT NULL foreign key.

        A panic press with no session gets an anonymous one — an unregistered
        pilgrim must still be able to call for help. Returns None only when the
        database is unreachable, in which case nothing is being persisted anyway.
        """
        state = await self.sessions.resolve(
            session_id, user_id=user_id, language=language, channel=channel
        )
        return state.session_id if await self.sessions.ensure_row(state) else None

    @staticmethod
    def _to_admin(event: SosEvent) -> SosEventAdmin:
        age = (now_utc() - event.created_at).total_seconds() / 60
        return SosEventAdmin(
            sos_id=event.id,
            status=event.status,  # type: ignore[arg-type]
            session_id=event.session_id,
            latitude=event.latitude,
            longitude=event.longitude,
            channel=event.channel,  # type: ignore[arg-type]
            notes=event.notes,
            created_at=event.created_at,
            resolved_at=event.resolved_at,
            age_minutes=max(0, int(age)),
        )

    @staticmethod
    def _notes(
        emergency_type: str,
        desk: str,
        eta: int | None,
        phone: str | None,
        description: str | None,
        accuracy_m: float | None,
    ) -> str:
        """Dispatch detail the control room reads back.

        `sos_events` has a single free-text `notes` column, so the operational
        fields are folded in as `key=value` lines rather than dropped.
        """
        parts = [f"type={emergency_type}", f"dispatched_to={desk}"]
        parts.append(f"eta_minutes={eta}" if eta is not None else "location=unknown")
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
