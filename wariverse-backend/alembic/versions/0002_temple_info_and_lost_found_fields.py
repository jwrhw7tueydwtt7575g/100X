"""Editable temple info, plus last-seen location and session on lost & found

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = sa.Uuid(as_uuid=True)
_TS = sa.DateTime(timezone=True)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- temple_info -------------------------------------------------------
    op.create_table(
        "temple_info",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("language", sa.String(2), nullable=False, server_default="en"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("timings", sa.String(120), nullable=False),
        sa.Column("rituals", _JSONB, nullable=False),
        sa.Column("events", _JSONB, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    # One row per language: `unique=True, index=True` on the model is a single
    # unique index, so create exactly that or autogenerate will drift.
    op.create_index("ix_temple_info_language", "temple_info", ["language"], unique=True)
    op.create_index("ix_temple_info_created_at", "temple_info", ["created_at"])

    # --- lost_found_reports ------------------------------------------------
    op.add_column(
        "lost_found_reports", sa.Column("last_seen_location", sa.String(255))
    )
    op.add_column("lost_found_reports", sa.Column("session_id", _UUID))
    op.create_foreign_key(
        "fk_lost_found_reports_session_id",
        "lost_found_reports",
        "sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_lost_found_reports_session_id", "lost_found_reports", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_lost_found_reports_session_id", table_name="lost_found_reports")
    op.drop_constraint(
        "fk_lost_found_reports_session_id", "lost_found_reports", type_="foreignkey"
    )
    op.drop_column("lost_found_reports", "session_id")
    op.drop_column("lost_found_reports", "last_seen_location")
    op.drop_table("temple_info")
