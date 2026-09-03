"""daemon registration

Revision ID: e3a9e1a782a4
Revises: be3043cc6d1f
Create Date: 2026-09-01 22:59:01.283046

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a9e1a782a4"
down_revision: str | Sequence[str] | None = "be3043cc6d1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """What a daemon last told the controller about itself."""
    op.create_table(
        "daemons",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("storages", sa.ARRAY(sa.String(length=255)), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("daemons")
