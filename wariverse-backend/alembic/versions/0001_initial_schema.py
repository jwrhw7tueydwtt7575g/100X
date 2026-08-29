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
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- 1. users ----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("phone_number", sa.String(15), nullable=False),
        sa.Column("name", sa.String(120)),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    # `unique=True, index=True` on the model is ONE unique index, not an index
    # plus a separate constraint — keep them identical or autogenerate drifts.
    op.create_index("ix_users_phone_number", "users", ["phone_number"], unique=True)
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # --- 2. sessions -------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID),
        sa.Column("session_token", sa.String(64), nullable=False),
        sa.Column("language", sa.String(2), nullable=False, server_default="mr"),
        sa.Column("channel", sa.String(8), nullable=False, server_default="app"),
        sa.Column("context_json", _JSONB),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_active_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("channel in ('app', 'ivr')", name="ck_sessions_channel"),
    )
    op.create_index("ix_sessions_session_token", "sessions", ["session_token"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_created_at", "sessions", ["created_at"])
    op.create_index("ix_sessions_last_active_at", "sessions", ["last_active_at"])

    # --- 3. messages -------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("widgets_json", _JSONB),
        sa.Column("language", sa.String(2), nullable=False, server_default="mr"),
        sa.Column("is_voice", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "role in ('user', 'assistant', 'system')", name="ck_messages_role"
        ),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_session_created", "messages", ["session_id", "created_at"])

    # --- 4. crowd_density_readings -----------------------------------------
    op.create_table(
        "crowd_density_readings",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("zone_id", sa.String(50), nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("density", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("recorded_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.String(16), nullable=False, server_default="model"),
        sa.CheckConstraint("density between 0 and 100", name="ck_crowd_density_range"),
        sa.CheckConstraint(
            "status in ('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH')", name="ck_crowd_status"
        ),
        sa.CheckConstraint(
            "source in ('camera', 'manual', 'model')", name="ck_crowd_source"
        ),
    )
    op.create_index(
        "ix_crowd_readings_zone_recorded",
        "crowd_density_readings",
        ["zone_id", "recorded_at"],
    )

    # --- 5. sos_events -----------------------------------------------------
    op.create_table(
        "sos_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", _TS),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status in ('PENDING', 'ACTIVATED', 'RESOLVED')", name="ck_sos_status"
        ),
    )
    op.create_index("ix_sos_events_session_id", "sos_events", ["session_id"])
    op.create_index("ix_sos_events_user_id", "sos_events", ["user_id"])
    op.create_index("ix_sos_events_status", "sos_events", ["status"])
    op.create_index("ix_sos_events_created_at", "sos_events", ["created_at"])

    # --- 6. lost_found_reports ---------------------------------------------
    # Reference ids are handed out sequentially (WF-2026-00124) so two desks
    # filing at once can never mint the same one.
    op.execute("CREATE SEQUENCE IF NOT EXISTS lost_found_reference_seq START WITH 1")
    op.create_table(
        "lost_found_reports",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("reference_id", sa.String(20), nullable=False),
        sa.Column("incident_type", sa.String(8), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("reporter_phone", sa.String(15), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "incident_type in ('PERSON', 'ITEM')", name="ck_lost_found_incident_type"
        ),
        sa.CheckConstraint(
            "status in ('OPEN', 'IN_PROGRESS', 'MATCHED', 'RESOLVED', 'CLOSED')",
            name="ck_lost_found_status",
        ),
    )
    op.create_index(
        "ix_lost_found_reports_reference_id", "lost_found_reports", ["reference_id"],
        unique=True,
    )
    op.create_index("ix_lost_found_reports_reporter_phone", "lost_found_reports",
                    ["reporter_phone"])
    op.create_index("ix_lost_found_reports_status", "lost_found_reports", ["status"])
    op.create_index("ix_lost_found_reports_created_at", "lost_found_reports", ["created_at"])

    # --- 7. otp_codes ------------------------------------------------------
    op.create_table(
        "otp_codes",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("phone_number", sa.String(15), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_otp_codes_phone_number", "otp_codes", ["phone_number"])
    op.create_index("ix_otp_codes_created_at", "otp_codes", ["created_at"])
    op.create_index("ix_otp_codes_phone_expires", "otp_codes", ["phone_number", "expires_at"])

    # --- supporting tables --------------------------------------------------
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
        sa.Column("wheelchair_accessible", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("details", _JSONB),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("external_id", name="uq_facilities_external_id"),
    )
    op.create_index("ix_facilities_facility_type", "facilities", ["facility_type"])
    op.create_index("ix_facilities_lat_lon", "facilities", ["lat", "lon"])
    op.create_index("ix_facilities_created_at", "facilities", ["created_at"])

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
        sa.Column("zone_ref", sa.String(50)),
        sa.Column("is_halt", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("landmark", sa.String(255)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("route_id", "sequence", name="uq_route_sequence"),
    )
    op.create_index("ix_route_waypoints_route_id", "route_waypoints", ["route_id"])
    op.create_index("ix_route_waypoints_created_at", "route_waypoints", ["created_at"])

    op.create_table(
        "temple_notices",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("language", sa.String(2), nullable=False, server_default="mr"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("active_from", _TS),
        sa.Column("active_until", _TS),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_temple_notices_created_at", "temple_notices", ["created_at"])


def downgrade() -> None:
    op.drop_table("temple_notices")
    op.drop_table("route_waypoints")
    op.drop_table("facilities")
    op.drop_table("otp_codes")
    op.drop_table("lost_found_reports")
    op.execute("DROP SEQUENCE IF EXISTS lost_found_reference_seq")
    op.drop_table("sos_events")
    op.drop_table("crowd_density_readings")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("users")
