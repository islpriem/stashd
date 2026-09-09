"""What was moved, and what is held.

Both reports read the database only. The usage report groups terminal transfers inside a
window; the allocation report is a snapshot of live filesets against the limits that
apply to them.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.filesets import FilesetState
from stashd.domain.transfers import TransferState
from stashd.models import Fileset, Transfer, TransferStat
from stashd.services import allocations

TERMINAL = (TransferState.SUCCEEDED, TransferState.FAILED, TransferState.CANCELLED)


class GroupBy(StrEnum):
    USER = "user"
    STORAGE = "storage"
    ROUTE = "route"
    LOCATION = "location"


class InvalidRequest(StashError):
    code = ErrorCode.INVALID_REQUEST


@dataclass(frozen=True, slots=True)
class UsageGroup:
    key: str
    bytes_transferred: int
    transfers: int
    succeeded: int
    success_rate: float
    mean_queue_wait_seconds: float
    p95_queue_wait_seconds: float
    mean_throughput_bytes_per_s: float


@dataclass(frozen=True, slots=True)
class AllocationRow:
    user: str
    storage_id: str
    allocated_bytes: int
    used_bytes: int
    filesets: int
    limit_bytes: int


@dataclass(frozen=True, slots=True)
class Offender:
    user: str
    storage_id: str
    fileset: str
    allocated_bytes: int
    used_bytes: int


def group_by_from(value: str) -> GroupBy:
    try:
        return GroupBy(value)
    except ValueError:
        raise InvalidRequest(
            f"cannot group by {value!r}: use {', '.join(item.value for item in GroupBy)}",
            group_by=value,
        ) from None


def _key(cluster: ClusterConfig, group_by: GroupBy, transfer: Transfer, storage_id: str) -> str:
    if group_by is GroupBy.USER:
        return transfer.user
    if group_by is GroupBy.ROUTE:
        return transfer.route
    if group_by is GroupBy.STORAGE:
        return storage_id
    try:
        return str(cluster.storage(storage_id).location)
    except KeyError:  # a storage that has left the config still has history
        return storage_id


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank: with few samples it names one of them rather than inventing one."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(fraction * len(ordered) + 0.5)))
    return ordered[rank - 1]


async def usage_report(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    group_by: GroupBy,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[UsageGroup]:
    query = (
        sa.select(Transfer, Fileset.storage_id)
        .join(Fileset, Fileset.id == Transfer.fileset_id)
        .where(Transfer.state.in_(TERMINAL))
    )
    if since is not None:
        query = query.where(Transfer.submitted_at >= since)
    if until is not None:
        query = query.where(Transfer.submitted_at < until)

    waits: dict[str, list[float]] = {}
    totals: dict[str, dict[str, float]] = {}
    for transfer, storage_id in await session.execute(query):
        key = _key(cluster, group_by, transfer, storage_id)
        row = _bucket(totals, key)
        row["bytes"] += transfer.bytes_done
        row["transfers"] += 1
        if transfer.state is TransferState.SUCCEEDED:
            row["succeeded"] += 1
        if transfer.started_at is not None:
            waited = (transfer.started_at - transfer.submitted_at).total_seconds()
            waits.setdefault(key, []).append(waited)
            row["waited"] += waited
            row["counted_waits"] += 1
            if transfer.finished_at is not None:
                row["seconds"] += (transfer.finished_at - transfer.started_at).total_seconds()

    await _add_rolled_up(session, cluster, group_by, totals, since, until)

    report = []
    for key, row in sorted(totals.items()):
        seen = waits.get(key, [])
        report.append(
            UsageGroup(
                key=key,
                bytes_transferred=int(row["bytes"]),
                transfers=int(row["transfers"]),
                succeeded=int(row["succeeded"]),
                success_rate=row["succeeded"] / row["transfers"] if row["transfers"] else 0.0,
                mean_queue_wait_seconds=row["waited"] / row["counted_waits"]
                if row["counted_waits"]
                else 0.0,
                # Only unpruned transfers keep their individual waits; a rolled-up day
                # carries the sum, not the distribution.
                p95_queue_wait_seconds=_percentile(seen, 0.95),
                mean_throughput_bytes_per_s=row["bytes"] / row["seconds"]
                if row["seconds"]
                else 0.0,
            )
        )
    return report


def _bucket(totals: dict[str, dict[str, float]], key: str) -> dict[str, float]:
    return totals.setdefault(
        key,
        {
            "bytes": 0.0,
            "transfers": 0.0,
            "succeeded": 0.0,
            "seconds": 0.0,
            "waited": 0.0,
            "counted_waits": 0.0,
        },
    )


async def _add_rolled_up(
    session: AsyncSession,
    cluster: ClusterConfig,
    group_by: GroupBy,
    totals: dict[str, dict[str, float]],
    since: datetime | None,
    until: datetime | None,
) -> None:
    """What pruning kept, added to what is still in the transfers table."""
    query = sa.select(TransferStat)
    if since is not None:
        query = query.where(TransferStat.day >= since.date())
    if until is not None:
        query = query.where(TransferStat.day < until.date())
    for stat in await session.scalars(query):
        key = _rolled_up_key(cluster, group_by, stat)
        row = _bucket(totals, key)
        row["bytes"] += stat.bytes_transferred
        row["transfers"] += stat.transfers
        row["succeeded"] += stat.succeeded
        row["seconds"] += stat.running_seconds
        row["waited"] += stat.queue_wait_seconds
        row["counted_waits"] += stat.transfers


def _rolled_up_key(cluster: ClusterConfig, group_by: GroupBy, stat: TransferStat) -> str:
    if group_by is GroupBy.USER:
        return stat.user
    if group_by is GroupBy.ROUTE:
        return stat.route
    if group_by is GroupBy.STORAGE:
        return stat.storage_id
    try:
        return str(cluster.storage(stat.storage_id).location)
    except KeyError:  # a storage that has left the config still has history
        return stat.storage_id


async def allocation_report(
    session: AsyncSession, *, cluster: ClusterConfig
) -> tuple[list[AllocationRow], list[Offender]]:
    live = sa.select(Fileset).where(Fileset.state != FilesetState.RELEASED)
    grouped = (
        sa.select(
            Fileset.owner_user,
            Fileset.storage_id,
            sa.func.sum(Fileset.allocated_bytes),
            sa.func.sum(Fileset.used_bytes),
            sa.func.count(),
        )
        .where(Fileset.state != FilesetState.RELEASED)
        .group_by(Fileset.owner_user, Fileset.storage_id)
        .order_by(Fileset.owner_user, Fileset.storage_id)
    )
    rows = []
    for user, storage_id, allocated, used, count in await session.execute(grouped):
        limits = await allocations.limits_of(session, user)
        rows.append(
            AllocationRow(
                user=user,
                storage_id=storage_id,
                allocated_bytes=int(allocated),
                used_bytes=int(used),
                filesets=int(count),
                limit_bytes=allocations.storage_limit(cluster, storage_id, limits),
            )
        )
    offenders = [
        Offender(
            user=fileset.owner_user,
            storage_id=fileset.storage_id,
            fileset=fileset.name,
            allocated_bytes=fileset.allocated_bytes,
            used_bytes=fileset.used_bytes,
        )
        for fileset in await session.scalars(
            live.where(Fileset.over_allocation.is_(True)).order_by(
                Fileset.owner_user, Fileset.name
            )
        )
    ]
    return rows, offenders
