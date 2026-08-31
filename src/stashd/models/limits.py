"""Per-user allocation limits; a NULL storage_id is the cluster-wide one."""

from sqlalchemy import Index, text
from sqlalchemy.orm import Mapped, mapped_column

from stashd.models.base import Base, Bytes, Name


class UserLimit(Base):
    __tablename__ = "user_limits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user: Mapped[Name]
    storage_id: Mapped[Name | None] = mapped_column(default=None)
    allocation_limit_bytes: Mapped[Bytes]

    __table_args__ = (
        Index("uq_user_limits_storage", "user", "storage_id", unique=True),
        Index(
            "uq_user_limits_cluster_wide",
            "user",
            unique=True,
            postgresql_where=text("storage_id IS NULL"),
        ),
    )
