"""flush releases after

Revision ID: c41b8f2a7e13
Revises: 1d5dc9445d52
Create Date: 2026-09-02 13:20:11.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c41b8f2a7e13"
down_revision: str | Sequence[str] | None = "1d5dc9445d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Whether a successful flush releases the fileset it wrote out."""
    op.add_column(
        "transfers",
        sa.Column("release_after", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("transfers", "release_after")
