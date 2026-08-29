"""SQLAlchemy 2.x ORM models.

Geospatial columns are plain float lat/lon rather than PostGIS geometry: the
whole Wari corridor fits in a ~250 km box, so a bounding-box prefilter plus a
haversine distance in Python is accurate enough and keeps the deployment to a
stock Postgres image. Swap in PostGIS if radius search ever becomes the
bottleneck.
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


# --- identity ---------------------------------------------------------------


class User(Base):
    """A pilgrim. Phone number is the only identifier we collect."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    preferred_language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
    )
    dindi_name: Mapped[str | None] = mapped_column(String(160))
    emergency_contact: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    sessions: Mapped[list[ConversationSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OtpRequest(Base):
    """Audit trail for OTP delivery. The code itself lives only in Redis."""

    __tablename__ = "otp_requests"

    id: Mapped[uuid.UUID] = _pk()
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, default="sms", server_default="sms"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (Index("ix_otp_requests_phone_created", "phone", "created_at"),)


# --- conversation -----------------------------------------------------------


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
    )
    last_intent: Mapped[str | None] = mapped_column(String(32))
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lon: Mapped[float | None] = mapped_column(Float)
    pending_sos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    user: Mapped[User | None] = relationship(back_populates="sessions")
    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ConversationMessage.created_at"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
    )
    intent: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()

    session: Mapped[ConversationSession] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'system')", name="ck_message_role"),
    )


# --- crowd ------------------------------------------------------------------


class Zone(Base):
    """A monitored area: ghat, temple queue, palkhi halt, parking lot."""

    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = _pk()
    zone_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_mr: Mapped[str | None] = mapped_column(String(160))
    name_hi: Mapped[str | None] = mapped_column(String(160))
    zone_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="general", server_default="general"
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_m: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200, server_default="200"
    )
    capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5000, server_default="5000"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    alternate_zone_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    snapshots: Mapped[list[CrowdSnapshot]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )


class CrowdSnapshot(Base):
    """One density reading, written by the CCTV/drone ingestion pipeline."""

    __tablename__ = "crowd_snapshots"

    id: Mapped[uuid.UUID] = _pk()
    zone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False
    )
    people_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    density_per_sqm: Mapped[float | None] = mapped_column(Float)
    density_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="low", server_default="low"
    )
    wait_minutes: Mapped[int | None] = mapped_column(Integer)
    trend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="steady", server_default="steady"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="cctv", server_default="cctv"
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    zone: Mapped[Zone] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_crowd_snapshots_zone_recorded", "zone_id", "recorded_at"),
        CheckConstraint(
            "density_level in ('low', 'moderate', 'high', 'critical')",
            name="ck_snapshot_density_level",
        ),
    )


# --- facilities & routes ----------------------------------------------------


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
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (Index("ix_facilities_lat_lon", "lat", "lon"),)


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
    zone_ref: Mapped[str | None] = mapped_column(String(64))
    is_halt: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    landmark: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("route_id", "sequence", name="uq_route_sequence"),)


class TempleNotice(Base):
    """Time-bound announcement shown alongside the static temple information."""

    __tablename__ = "temple_notices"

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
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


# --- safety -----------------------------------------------------------------


class LostFoundReport(Base):
    __tablename__ = "lost_found_reports"

    id: Mapped[uuid.UUID] = _pk()
    ref_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)  # person | item
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open", index=True
    )
    subject_name: Mapped[str | None] = mapped_column(String(160))
    subject_age: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    distinguishing_marks: Mapped[str | None] = mapped_column(Text)
    last_seen_location: Mapped[str | None] = mapped_column(String(255))
    last_seen_lat: Mapped[float | None] = mapped_column(Float)
    last_seen_lon: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reporter_name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
    )
    assigned_desk: Mapped[str | None] = mapped_column(String(120))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        CheckConstraint("report_type in ('person', 'item')", name="ck_lost_found_type"),
        CheckConstraint(
            "status in ('open', 'in_progress', 'matched', 'resolved', 'closed')",
            name="ck_lost_found_status",
        ),
    )


class SosEvent(Base):
    __tablename__ = "sos_events"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    emergency_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="other", server_default="other"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="dispatched",
        server_default="dispatched",
        index=True,
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="mr", server_default="mr"
    )
    nearest_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("facilities.id", ondelete="SET NULL")
    )
    dispatched_to: Mapped[str | None] = mapped_column(String(160))
    eta_minutes: Mapped[int | None] = mapped_column(Integer)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'dispatched', 'acknowledged', 'resolved', 'cancelled')",
            name="ck_sos_status",
        ),
    )
