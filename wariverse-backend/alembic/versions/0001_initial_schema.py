"""Initial WariVerse schema

Revision ID: 0001
Revises:
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.Uuid(as_uuid=True)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # --- users -------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("preferred_language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("dindi_name", sa.String(160)),
        sa.Column("emergency_contact", sa.String(20)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", _TS),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    # `unique=True, index=True` on the model is one unique index, not an index
    # plus a separate constraint — keep them identical or autogenerate drifts.
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # --- otp_requests ------------------------------------------------------
    op.create_table(
        "otp_requests",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="sms"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verified_at", _TS),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_otp_requests_phone", "otp_requests", ["phone"])
    op.create_index("ix_otp_requests_created_at", "otp_requests", ["created_at"])
    op.create_index("ix_otp_requests_phone_created", "otp_requests", ["phone", "created_at"])

    # --- zones -------------------------------------------------------------
    op.create_table(
        "zones",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("zone_id", sa.String(64), nullable=False),
        sa.Column("name_en", sa.String(160), nullable=False),
        sa.Column("name_mr", sa.String(160)),
        sa.Column("name_hi", sa.String(160)),
        sa.Column("zone_type", sa.String(32), nullable=False, server_default="general"),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("radius_m", sa.Integer, nullable=False, server_default="200"),
        sa.Column("capacity", sa.Integer, nullable=False, server_default="5000"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("alternate_zone_ids", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_zones_zone_id", "zones", ["zone_id"], unique=True)
    op.create_index("ix_zones_created_at", "zones", ["created_at"])

    # --- crowd_snapshots ---------------------------------------------------
    op.create_table(
        "crowd_snapshots",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("zone_id", _UUID, nullable=False),
        sa.Column("people_estimate", sa.Integer, nullable=False),
        sa.Column("density_per_sqm", sa.Float),
        sa.Column("density_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("wait_minutes", sa.Integer),
        sa.Column("trend", sa.String(16), nullable=False, server_default="steady"),
        sa.Column("source", sa.String(32), nullable=False, server_default="cctv"),
        sa.Column("recorded_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "density_level in ('low', 'moderate', 'high', 'critical')",
            name="ck_snapshot_density_level",
        ),
    )
    op.create_index(
        "ix_crowd_snapshots_zone_recorded", "crowd_snapshots", ["zone_id", "recorded_at"]
    )

    # --- facilities --------------------------------------------------------
    op.create_table(
        "facilities",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("external_id", sa.String(64)),
        sa.Column("name_en", sa.String(160), nullable=False),
        sa.Column("name_mr", sa.String(160)),
        sa.Column("name_hi", sa.String(160)),
        sa.Column("facility_type", sa.String(32), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("address", sa.String(255)),
        sa.Column("contact_phone", sa.String(20)),
        sa.Column("opens_at", sa.String(5)),
        sa.Column("closes_at", sa.String(5)),
        sa.Column("is_24x7", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_operational", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("capacity", sa.Integer),
        sa.Column(
            "wheelchair_accessible", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("external_id", name="uq_facilities_external_id"),
    )
    op.create_index("ix_facilities_facility_type", "facilities", ["facility_type"])
    op.create_index("ix_facilities_lat_lon", "facilities", ["lat", "lon"])
    op.create_index("ix_facilities_created_at", "facilities", ["created_at"])

    # --- route_waypoints ---------------------------------------------------
    op.create_table(
        "route_waypoints",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("route_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("name_en", sa.String(160), nullable=False),
        sa.Column("name_mr", sa.String(160)),
        sa.Column("name_hi", sa.String(160)),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("zone_ref", sa.String(64)),
        sa.Column("is_halt", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("landmark", sa.String(255)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("route_id", "sequence", name="uq_route_sequence"),
    )
    op.create_index("ix_route_waypoints_route_id", "route_waypoints", ["route_id"])
    op.create_index("ix_route_waypoints_created_at", "route_waypoints", ["created_at"])

    # --- temple_notices ----------------------------------------------------
    op.create_table(
        "temple_notices",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("active_from", _TS),
        sa.Column("active_until", _TS),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_temple_notices_created_at", "temple_notices", ["created_at"])

    # --- conversation ------------------------------------------------------
    op.create_table(
        "conversation_sessions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("last_intent", sa.String(32)),
        sa.Column("last_lat", sa.Float),
        sa.Column("last_lon", sa.Float),
        sa.Column("pending_sos", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversation_sessions_user_id", "conversation_sessions", ["user_id"])
    op.create_index("ix_conversation_sessions_created_at", "conversation_sessions", ["created_at"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("intent", sa.String(32)),
        sa.Column("confidence", sa.Float),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("model", sa.String(64)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["session_id"], ["conversation_sessions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("role in ('user', 'assistant', 'system')", name="ck_message_role"),
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])

    # --- lost_found_reports ------------------------------------------------
    op.create_table(
        "lost_found_reports",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("ref_id", sa.String(16), nullable=False),
        sa.Column("report_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("subject_name", sa.String(160)),
        sa.Column("subject_age", sa.Integer),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("distinguishing_marks", sa.Text),
        sa.Column("last_seen_location", sa.String(255)),
        sa.Column("last_seen_lat", sa.Float),
        sa.Column("last_seen_lon", sa.Float),
        sa.Column("last_seen_at", _TS),
        sa.Column("reporter_name", sa.String(160), nullable=False),
        sa.Column("contact_phone", sa.String(20), nullable=False),
        sa.Column("photo_url", sa.String(500)),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("assigned_desk", sa.String(120)),
        sa.Column("resolution_note", sa.Text),
        sa.Column("resolved_at", _TS),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("report_type in ('person', 'item')", name="ck_lost_found_type"),
        sa.CheckConstraint(
            "status in ('open', 'in_progress', 'matched', 'resolved', 'closed')",
            name="ck_lost_found_status",
        ),
    )
    op.create_index("ix_lost_found_reports_ref_id", "lost_found_reports", ["ref_id"], unique=True)
    op.create_index("ix_lost_found_reports_status", "lost_found_reports", ["status"])
    op.create_index("ix_lost_found_reports_contact_phone", "lost_found_reports", ["contact_phone"])
    op.create_index("ix_lost_found_reports_created_at", "lost_found_reports", ["created_at"])

    # --- sos_events --------------------------------------------------------
    op.create_table(
        "sos_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID),
        sa.Column("session_id", _UUID),
        sa.Column("phone", sa.String(20)),
        sa.Column("emergency_type", sa.String(24), nullable=False, server_default="other"),
        sa.Column("status", sa.String(16), nullable=False, server_default="dispatched"),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("accuracy_m", sa.Float),
        sa.Column("description", sa.Text),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("nearest_facility_id", _UUID),
        sa.Column("dispatched_to", sa.String(160)),
        sa.Column("eta_minutes", sa.Integer),
        sa.Column("acknowledged_at", _TS),
        sa.Column("resolved_at", _TS),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["nearest_facility_id"], ["facilities.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status in ('pending', 'dispatched', 'acknowledged', 'resolved', 'cancelled')",
            name="ck_sos_status",
        ),
    )
    op.create_index("ix_sos_events_user_id", "sos_events", ["user_id"])
    op.create_index("ix_sos_events_session_id", "sos_events", ["session_id"])
    op.create_index("ix_sos_events_status", "sos_events", ["status"])
    op.create_index("ix_sos_events_created_at", "sos_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("sos_events")
    op.drop_table("lost_found_reports")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("temple_notices")
    op.drop_table("route_waypoints")
    op.drop_table("facilities")
    op.drop_table("crowd_snapshots")
    op.drop_table("zones")
    op.drop_table("otp_requests")
    op.drop_table("users")
