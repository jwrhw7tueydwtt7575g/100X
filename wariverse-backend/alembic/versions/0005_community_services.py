"""Community seva offerings published by volunteers and residents

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "community_services",
        # A readable id (`cs-9f2c1a...`) rather than a bare UUID: providers read
        # it back to themselves when asking a volunteer for help.
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("available_from", _TS, nullable=False),
        sa.Column("available_until", _TS, nullable=False),
        sa.Column("contact_phone", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("user_id", sa.Uuid(as_uuid=True)),
        sa.Column("owner_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "category in ('food', 'accommodation', 'water', 'medical', 'rest')",
            name="ck_community_category",
        ),
        sa.CheckConstraint(
            "available_until > available_from", name="ck_community_window"
        ),
    )
    op.create_index("ix_community_services_category", "community_services", ["category"])
    op.create_index("ix_community_services_is_active", "community_services", ["is_active"])
    op.create_index("ix_community_services_user_id", "community_services", ["user_id"])
    op.create_index("ix_community_services_created_at", "community_services", ["created_at"])
    op.create_index(
        "ix_community_active_window", "community_services", ["is_active", "available_until"]
    )
    op.create_index("ix_community_lat_lon", "community_services", ["latitude", "longitude"])


def downgrade() -> None:
    op.drop_table("community_services")
