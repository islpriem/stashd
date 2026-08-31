"""The fileset table."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.models.base import Base, Bytes, Count, Name, enum_column


class Fileset(Base):
    __tablename__ = "filesets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[Name]
    owner_user: Mapped[Name]
    owner_uid: Mapped[Count]
    owner_gid: Mapped[Count]
    storage_id: Mapped[Name]
    kind: Mapped[FilesetKind] = enum_column(FilesetKind)
    state: Mapped[FilesetState] = enum_column(FilesetState)
    path: Mapped[str]
    allocated_bytes: Mapped[Bytes]
    used_bytes: Mapped[Bytes] = mapped_column(default=0)
    used_bytes_at: Mapped[datetime | None] = mapped_column(default=None)
    file_count: Mapped[int | None] = mapped_column(default=None)
    over_allocation: Mapped[bool] = mapped_column(default=False)

    # A cached fileset carries its origin for its whole life, and has exactly one.
    source_storage_id: Mapped[Name | None] = mapped_column(default=None)
    source_path: Mapped[str | None] = mapped_column(default=None)

    # Summary history; the per-operation history is in transfers.
    created_at: Mapped[datetime]
    warm_started_at: Mapped[datetime | None] = mapped_column(default=None)
    warm_finished_at: Mapped[datetime | None] = mapped_column(default=None)
    last_flushed_at: Mapped[datetime | None] = mapped_column(default=None)
    last_flush_target: Mapped[str | None] = mapped_column(default=None)
    released_at: Mapped[datetime | None] = mapped_column(default=None)
    last_transfer_id: Mapped[int | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint("allocated_bytes > 0", name="ck_filesets_allocation_positive"),
        CheckConstraint("used_bytes >= 0", name="ck_filesets_used_not_negative"),
        # A released name becomes reusable, so uniqueness is partial.
        Index(
            "uq_filesets_live_name",
            "owner_user",
            "storage_id",
            "name",
            unique=True,
            postgresql_where=text("state <> 'RELEASED'"),
        ),
        Index("ix_filesets_storage_owner", "storage_id", "owner_user"),
        Index("ix_filesets_state", "state"),
    )
