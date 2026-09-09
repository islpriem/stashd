"""transfer stats

Revision ID: e7a2c9d31b45
Revises: c41b8f2a7e13
Create Date: 2026-09-02 14:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a2c9d31b45"
down_revision: str | Sequence[str] | None = "c41b8f2a7e13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The daily buckets that outlive pruned transfers."""
    op.create_table(
        "transfer_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user", sa.String(length=255), nullable=False),
        sa.Column("storage_id", sa.String(length=255), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("warm", "flush", "release", name="transferkind", native_enum=False),
            nullable=False,
        ),
        sa.Column("transfers", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("bytes_transferred", sa.BigInteger(), nullable=False),
        sa.Column("queue_wait_seconds", sa.BigInteger(), nullable=False),
        sa.Column("running_seconds", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_transfer_stats_bucket",
        "transfer_stats",
        ["day", "user", "storage_id", "route", "kind"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_transfer_stats_bucket", table_name="transfer_stats")
    op.drop_table("transfer_stats")
