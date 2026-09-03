"""transfer event sequence

Revision ID: be3043cc6d1f
Revises: 1da451ab0e43
Create Date: 2026-09-01 22:44:55.812674

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be3043cc6d1f"
down_revision: str | Sequence[str] | None = "1da451ab0e43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Order daemon events; duplicates and late arrivals must not move a transfer back."""
    op.add_column(
        "transfers",
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("transfers", "last_sequence", server_default=None)


def downgrade() -> None:
    op.drop_column("transfers", "last_sequence")
