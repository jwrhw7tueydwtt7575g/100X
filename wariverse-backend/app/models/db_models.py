"""SQLAlchemy 2.x ORM models (async-ready, `Mapped[...]` style).

Seven tables come from the WariVerse data spec: `users`, `sessions`,
`messages`, `crowd_density_readings`, `sos_events`, `lost_found_reports` and
`otp_codes`.

Three more (`facilities`, `route_waypoints`, `temple_notices`) are not in that
spec but back the `/api/facilities/nearby`, `/api/routes/guidance` and
`/api/temple/info` endpoints, so they are kept.

There is deliberately no `zones` table: `crowd_density_readings` carries
`zone_id`, `zone_name` and coordinates inline, so a reading is self-describing
and the ingestion pipeline needs no join. Static zone metadata (localized
names, capacity, alternates) lives in `app/data/reference.py`.

Geospatial columns are plain float lat/lon rather than PostGIS geometry: the
Wari corridor fits in a ~250 km box, so a bounding-box prefilter plus a
haversine in Python is accurate enough on a stock Postgres image.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# SQLAlchemy's JSON types render a Python `None` as the JSON scalar `null`, not
# as SQL NULL — so `WHERE col IS NULL` would silently skip those rows. Every
# nullable JSONB column below uses this so "absent" means one thing.
NullableJSONB = JSONB(none_as_null=True)

# --- shared column helpers --------------------------------------------------


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# Sequence backing the human-readable lost & found reference ids.
LOST_FOUND_SEQUENCE = "lost_found_reference_seq"


# --- 1. users ---------------------------------------------------------------


class User(Base):
    """A pilgrim. The phone number is the only identifier collected."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    phone_number: Mapped[str] = mapped_column(
        String(15), unique=True, nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(
        String(2), nullable=False, default="en", server_default="en"
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    sos_events: Mapped[list[SosEvent]] = relationship(back_populates="user")


# --- 2. sessions ------------------------------------------------------------


class Session(Base):
    """A conversation, from the app or (once the gateway exists) from IVR.

    `user_id` is nullable: a pilgrim in trouble must be able to ask for help
    and raise an SOS without registering first.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # index=True as well as unique=True, so this is one unique *index* rather
    # than a constraint — the IVR gateway resumes a session by this token.
    session_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(
        String(2), nullable=False, default="mr", server_default="mr"
    )
    channel: Mapped[str] = mapped_column(
        String(8), nullable=False, default="app", server_default="app"
    )
    # Rolling LLM state: recent turns, pending-SOS flag, last known location and
    # last intent. See `SessionState` in app/services/session_service.py.
    context_json: Mapped[dict | None] = mapped_column(NullableJSONB)
    created_at: Mapped[datetime] = _created_at()
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        index=True,
    )

    user: Mapped[User | None] = relationship(back_populates="sessions")
    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )
    sos_events: Mapped[list[SosEvent]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("channel in ('app', 'ivr')", name="ck_sessions_channel"),
    )


# --- 3. messages ------------------------------------------------------------


class Message(Base):
    """One turn of a conversation.

    `widgets_json` holds the structured follow-up actions the client renders as
    buttons or deep links (confirm SOS, open the crowd map, …), so a transcript
    can be replayed exactly as the pilgrim saw it.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    widgets_json: Mapped[list | dict | None] = mapped_column(NullableJSONB)
    language: Mapped[str] = mapped_column(
        String(2), nullable=False, default="mr", server_default="mr"
    )
    is_voice: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role in ('user', 'assistant', 'system')", name="ck_messages_role"
        ),
        # Replaying one conversation in order is the dominant read.
        Index("ix_messages_session_created", "session_id", "created_at"),
    )


# --- 4. crowd_density_readings ----------------------------------------------


class CrowdDensityReading(Base):
    """One density reading for a zone, written by the ingestion pipeline.

    `density` is a 0-100 occupancy percentage; `status` is its bucket, stored
    alongside so that a consumer never has to re-derive the thresholds.
    """

    __tablename__ = "crowd_density_readings"

    id: Mapped[uuid.UUID] = _pk()
    zone_id: Mapped[str] = mapped_column(String(50), nullable=False)
    zone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    density: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="model", server_default="model"
    )

    __table_args__ = (
        CheckConstraint("density between 0 and 100", name="ck_crowd_density_range"),
        CheckConstraint(
            "status in ('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH')",
            name="ck_crowd_status",
        ),
        CheckConstraint(
            "source in ('camera', 'manual', 'model')", name="ck_crowd_source"
        ),
        # "latest reading for this zone" — the only hot query on this table.
        Index("ix_crowd_readings_zone_recorded", "zone_id", "recorded_at"),
    )


# --- 5. sos_events ----------------------------------------------------------


class SosEvent(Base):
    """An emergency raised from the panic button or confirmed in chat.

    Coordinates are nullable because an IVR caller may have none; the app-side
    API still requires them. `notes` carries the dispatch detail (emergency
    type, desk notified, ETA) that the control room reads back.
    """

    __tablename__ = "sos_events"

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING", index=True
    )
    # Where the emergency came from. A column rather than a note because the
    # control-room dashboard filters on it — an IVR caller cannot see a screen.
    channel: Mapped[str] = mapped_column(
        String(8), nullable=False, default="app", server_default="app"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[Session] = relationship(back_populates="sos_events")
    user: Mapped[User | None] = relationship(back_populates="sos_events")

    __table_args__ = (
        CheckConstraint(
            "status in ('PENDING', 'ACTIVATED', 'RESOLVED')", name="ck_sos_status"
        ),
        CheckConstraint("channel in ('app', 'ivr')", name="ck_sos_channel"),
    )


# --- 6. lost_found_reports --------------------------------------------------


class LostFoundReport(Base):
    """A missing person or lost item, mostly separated families.

    `reference_id` is the human-readable handle a pilgrim reads over a phone
    (`WF-2026-00124`), allocated from a Postgres sequence so concurrent desks
    never collide.
    """

    __tablename__ = "lost_found_reports"

    id: Mapped[uuid.UUID] = _pk()
    reference_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    incident_type: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reporter_phone: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    last_seen_location: Mapped[str | None] = mapped_column(String(255))
    # Where the reporter last saw them, when the phone could supply it. Far more
    # actionable for a search party than the free-text location alone.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # The chat session the report came from, so a volunteer can read the
    # conversation around it. Nullable: reports also arrive from the form and
    # from desks with no session at all.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="OPEN", server_default="OPEN", index=True
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        CheckConstraint(
            "incident_type in ('PERSON', 'ITEM')", name="ck_lost_found_incident_type"
        ),
        CheckConstraint(
            "status in ('OPEN', 'IN_PROGRESS', 'MATCHED', 'RESOLVED', 'CLOSED')",
            name="ck_lost_found_status",
        ),
    )


# --- 7. otp_codes -----------------------------------------------------------


class OtpCode(Base):
    """A login OTP.

    SECURITY: `code` stores the six digits in plaintext, as specified. Anyone
    with read access to this table — a replica, a backup, a dump, a SQL
    injection — can authenticate as any pilgrim who has a code in flight.
    The fix is small and does not change this schema: store an HMAC of
    `phone_number:code` keyed with JWT_SECRET and compare digests on verify
    (`app/routers/auth.py` already computes exactly that hash for its Redis
    fast path). Recommended before handling real numbers.

    The spec did not name a primary key; a UUID `id` is added because a table
    without one cannot be updated or replicated safely.
    """

    __tablename__ = "otp_codes"

    id: Mapped[uuid.UUID] = _pk()
    phone_number: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        # Verification looks up the newest unused, unexpired code for a number.
        Index("ix_otp_codes_phone_expires", "phone_number", "expires_at"),
    )


# --- supporting tables (not in the seven-table spec) ------------------------


class Facility(Base):
    """Water point, toilet block, medical camp, annachhatra, shelter, etc."""

    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = _pk()
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_mr: Mapped[str | None] = mapped_column(String(160))
    name_hi: Mapped[str | None] = mapped_column(String(160))
    facility_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    opens_at: Mapped[str | None] = mapped_column(String(5))  # "05:30"
    closes_at: Mapped[str | None] = mapped_column(String(5))
    is_24x7: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    is_operational: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    capacity: Mapped[int | None] = mapped_column(Integer)
    wheelchair_accessible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    details: Mapped[dict | None] = mapped_column(NullableJSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (Index("ix_facilities_lat_lon", "lat", "lon"),)


class CommunityService(Base):
    """A free offering published by a volunteer or resident along the route.

    Annachatras, rooms, water points and aid posts run by local people. These
    appear alongside the official facility directory in nearby search and on the
    map, flagged as seva so a pilgrim can tell who is offering what.

    `owner_token_hash` is what lets the provider take their own listing down.
    Without it, `DELETE /api/community/services/{id}` would be open to anyone —
    and the ids are handed out by the list endpoint, so a single sweep could
    erase every langar from the map. The token is shown once at creation and
    only its digest is stored.
    """

    __tablename__ = "community_services"

    id: Mapped[str] = mapped_column(
        String(24), primary_key=True, default=lambda: f"cs-{uuid.uuid4().hex[:12]}"
    )
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    available_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), index=True
    )
    # Set when a signed-in pilgrim publishes; anonymous providers rely on the
    # token alone.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    owner_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        CheckConstraint(
            "category in ('food', 'accommodation', 'water', 'medical', 'rest')",
            name="ck_community_category",
        ),
        CheckConstraint(
            "available_until > available_from", name="ck_community_window"
        ),
        # "What is on offer near here, now" — the only hot read.
        Index("ix_community_active_window", "is_active", "available_until"),
        Index("ix_community_lat_lon", "latitude", "longitude"),
    )


class RouteWaypoint(Base):
    """An ordered stop on a palkhi route (Alandi/Dehu → Pandharpur)."""

    __tablename__ = "route_waypoints"

    id: Mapped[uuid.UUID] = _pk()
    route_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_mr: Mapped[str | None] = mapped_column(String(160))
    name_hi: Mapped[str | None] = mapped_column(String(160))
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    zone_ref: Mapped[str | None] = mapped_column(String(50))
    is_halt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    landmark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("route_id", "sequence", name="uq_route_sequence"),)


class TempleInfo(Base):
    """The temple information card, editable by an operator.

    A single row per language rather than a hardcoded constant, so the Mandir
    Samiti can correct timings during the Wari without a redeploy.
    """

    __tablename__ = "temple_info"

    id: Mapped[uuid.UUID] = _pk()
    language: Mapped[str] = mapped_column(
        String(2), unique=True, nullable=False, index=True, default="en", server_default="en"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    timings: Mapped[str] = mapped_column(String(120), nullable=False)
    rituals: Mapped[list] = mapped_column(NullableJSONB, nullable=False)
    events: Mapped[list] = mapped_column(NullableJSONB, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class TempleNotice(Base):
    """Time-bound announcement shown alongside static temple information."""

    __tablename__ = "temple_notices"

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(2), nullable=False, default="mr", server_default="mr"
    )
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="info", server_default="info"
    )
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = _created_at()
