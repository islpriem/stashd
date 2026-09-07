"""retry and task id

Revision ID: 1d5dc9445d52
Revises: dd6ed37ae1cb
Create Date: 2026-09-02 12:01:01.992134

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d5dc9445d52"
down_revision: str | Sequence[str] | None = "dd6ed37ae1cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """What the daemon calls a transfer, and when a retry may be offered again."""
    op.add_column("transfers", sa.Column("task_id", sa.String(length=255), nullable=True))
    op.add_column(
        "transfers", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("transfers", "retry_after")
    op.drop_column("transfers", "task_id")
