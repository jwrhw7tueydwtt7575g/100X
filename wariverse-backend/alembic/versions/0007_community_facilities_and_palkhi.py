"""Community facilities and palkhi tracking tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-30

Three more models that reached the ORM without a migration. Same failure as
0006: SQLAlchemy selects every mapped column, so each query raised
UndefinedTable, aborted the request's transaction, and silently broke any write
that followed it in the same request.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "community_facilities",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("phone", sa.String(20)),
        sa.Column("type", sa.String(50), nullable=False, server_default="charity_food"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("added_by", sa.String(100)),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_community_facilities_category", "community_facilities", ["category"]
    )
    op.create_index(
        "ix_community_facilities_is_active", "community_facilities", ["is_active"]
    )
    op.create_index(
        "ix_community_facilities_created_at", "community_facilities", ["created_at"]
    )

    op.create_table(
        "palkhi_live_position",
        sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("current_place_name", sa.String(150), nullable=False),
        sa.Column("next_place_name", sa.String(150), nullable=False),
        sa.Column("eta_to_next", sa.Integer, nullable=False),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "palkhi_route_points",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("place_name", sa.String(150), nullable=False),
        sa.Column("scheduled_time", sa.String(50), nullable=False),
    )
    op.create_index(
        "ix_palkhi_route_points_sequence", "palkhi_route_points", ["sequence"]
    )


def downgrade() -> None:
    op.drop_index("ix_palkhi_route_points_sequence", table_name="palkhi_route_points")
    op.drop_table("palkhi_route_points")
    op.drop_table("palkhi_live_position")
    op.drop_index("ix_community_facilities_created_at", table_name="community_facilities")
    op.drop_index("ix_community_facilities_is_active", table_name="community_facilities")
    op.drop_index("ix_community_facilities_category", table_name="community_facilities")
    op.drop_table("community_facilities")
