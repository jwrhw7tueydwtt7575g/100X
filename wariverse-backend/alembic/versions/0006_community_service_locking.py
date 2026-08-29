"""Reservation ("lock") fields on community seva offerings

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-30

These columns were added to the ORM without a migration. Because SQLAlchemy
selects every mapped column, *every* `community_services` query failed with
UndefinedColumn — and since that query runs inside `facility_service.nearby()`,
it aborted the request's transaction, silently failing any write that came
after it. An SOS raised in the same request as a facility lookup would report
success and leave no row. Adding the columns closes that.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column(
        "community_services",
        sa.Column("is_locked", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "community_services", sa.Column("locked_by_user_id", sa.Uuid(as_uuid=True))
    )
    op.add_column("community_services", sa.Column("locked_by_name", sa.String(100)))
    op.add_column("community_services", sa.Column("locked_by_phone", sa.String(20)))
    op.add_column("community_services", sa.Column("locked_at", _TS))

    op.create_foreign_key(
        "fk_community_services_locked_by_user_id",
        "community_services",
        "users",
        ["locked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # "Which offerings are still free?" — the read the map and search do.
    op.create_index(
        "ix_community_services_is_locked", "community_services", ["is_locked"]
    )


def downgrade() -> None:
    op.drop_index("ix_community_services_is_locked", table_name="community_services")
    op.drop_constraint(
        "fk_community_services_locked_by_user_id",
        "community_services",
        type_="foreignkey",
    )
    for column in (
        "locked_at",
        "locked_by_phone",
        "locked_by_name",
        "locked_by_user_id",
        "is_locked",
    ):
        op.drop_column("community_services", column)
