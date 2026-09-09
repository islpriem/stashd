"""The transfer table."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from stashd.domain.transfers import TransferKind, TransferState
from stashd.models.base import Base, Bytes, Count, Name, enum_column


class Transfer(Base):
    __tablename__ = "transfers"

    # Monotonic integers from a sequence: users quote them like a Slurm job id.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[TransferKind] = enum_column(TransferKind)
    user: Mapped[Name]
    fileset_id: Mapped[int] = mapped_column(ForeignKey("filesets.id"))
    # The other end of the move, as a reference: HOT1:/myuser/dir for a warm.
    peer_ref: Mapped[str | None] = mapped_column(default=None)
    state: Mapped[TransferState] = enum_column(TransferState)
    route: Mapped[Name]
    bytes_total: Mapped[Bytes] = mapped_column(default=0)
    bytes_done: Mapped[Bytes] = mapped_column(default=0)
    files_total: Mapped[int | None] = mapped_column(default=None)
    files_done: Mapped[int | None] = mapped_column(default=None)
    executing_daemon_id: Mapped[Name | None] = mapped_column(default=None)
    bwlimit_bytes_per_s: Mapped[Bytes | None] = mapped_column(default=None)
    attempt: Mapped[Count] = mapped_column(default=1)
    # Daemon events arrive duplicated and out of order; this is what orders them.
    last_sequence: Mapped[Count] = mapped_column(default=0)
    # What the executing daemon calls this work, so it can be asked about it.
    task_id: Mapped[Name | None] = mapped_column(default=None)
    # A retry waits before it is offered again; submitted_at keeps its place.
    retry_after: Mapped[datetime | None] = mapped_column(default=None)
    # A flush releases the fileset when it succeeds, unless the user asked to keep it.
    release_after: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[Name | None] = mapped_column(default=None)
    error_detail: Mapped[str | None] = mapped_column(default=None)
    submitted_at: Mapped[datetime]
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        CheckConstraint("bytes_done <= bytes_total", name="ck_transfers_progress_within_total"),
        CheckConstraint("attempt >= 1", name="ck_transfers_attempt_positive"),
        Index("ix_transfers_state_submitted", "state", "submitted_at"),
        Index("ix_transfers_user_state", "user", "state"),
        Index("ix_transfers_fileset", "fileset_id"),
    )
