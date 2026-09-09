"""Rolled-up transfer statistics, which outlive the rows they came from."""

from datetime import date

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from stashd.domain.transfers import TransferKind
from stashd.models.base import Base, Bytes, Count, Name, enum_column


class TransferStat(Base):
    """One row per day, user, storage, route and kind: what pruning kept."""

    __tablename__ = "transfer_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day: Mapped[date]
    user: Mapped[Name]
    storage_id: Mapped[Name]
    route: Mapped[Name]
    kind: Mapped[TransferKind] = enum_column(TransferKind)
    transfers: Mapped[Count] = mapped_column(default=0)
    succeeded: Mapped[Count] = mapped_column(default=0)
    bytes_transferred: Mapped[Bytes] = mapped_column(default=0)
    queue_wait_seconds: Mapped[Bytes] = mapped_column(default=0)
    running_seconds: Mapped[Bytes] = mapped_column(default=0)

    __table_args__ = (
        Index(
            "uq_transfer_stats_bucket",
            "day",
            "user",
            "storage_id",
            "route",
            "kind",
            unique=True,
        ),
    )
