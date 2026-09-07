"""storage drain state

Revision ID: dd6ed37ae1cb
Revises: e3a9e1a782a4
Create Date: 2026-09-02 11:54:01.043712

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd6ed37ae1cb"
down_revision: str | Sequence[str] | None = "e3a9e1a782a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Whether a storage takes new work. Its topology stays in the cluster config."""
    op.create_table(
        "storage_states",
        sa.Column("storage_id", sa.String(length=255), nullable=False),
        sa.Column("drained", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("storage_id"),
    )


def downgrade() -> None:
    op.drop_table("storage_states")
