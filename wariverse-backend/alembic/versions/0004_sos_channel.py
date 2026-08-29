"""Record which channel an SOS arrived on

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows predate the column; they all came from the app.
    op.add_column(
        "sos_events",
        sa.Column("channel", sa.String(8), nullable=False, server_default="app"),
    )
    op.create_check_constraint(
        "ck_sos_channel", "sos_events", "channel in ('app', 'ivr')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_sos_channel", "sos_events", type_="check")
    op.drop_column("sos_events", "channel")
