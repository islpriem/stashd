"""Pruning what is past its retention, keeping what reports are built from.

Terminal transfers are rolled into a daily bucket before their rows go, so the usage
report still counts them afterwards. Audit events have their own retention and are
simply removed: they are a record of who did what, not a source of statistics.
"""

from datetime import datetime

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.transfers import TransferState
from stashd.models import AuditEvent, Fileset, Transfer, TransferStat

logger = structlog.get_logger()

TERMINAL = (TransferState.SUCCEEDED, TransferState.FAILED, TransferState.CANCELLED)


async def prune(session: AsyncSession, *, cluster: ClusterConfig, clock: Clock) -> int:
    """Roll up and remove what is past its retention. Returns transfers pruned."""
    now = clock.now()
    pruned = await _prune_transfers(session, now - cluster.retention.transfers)
    stale = list(
        await session.scalars(
            sa.select(AuditEvent.id).where(AuditEvent.ts < now - cluster.retention.audit)
        )
    )
    if stale:
        await session.execute(sa.delete(AuditEvent).where(AuditEvent.id.in_(stale)))
    await session.commit()
    if pruned or stale:
        logger.info("retention.pruned", transfers=pruned, audit_events=len(stale))
    return pruned


async def _prune_transfers(session: AsyncSession, before: datetime) -> int:
    rows = list(
        await session.execute(
            sa.select(Transfer, Fileset.storage_id)
            .join(Fileset, Fileset.id == Transfer.fileset_id)
            .where(Transfer.state.in_(TERMINAL), Transfer.submitted_at < before)
        )
    )
    if not rows:
        return 0

    for transfer, storage_id in rows:
        await _roll_up(session, transfer, storage_id)
    await session.execute(
        sa.delete(Transfer).where(Transfer.id.in_([transfer.id for transfer, _ in rows]))
    )
    return len(rows)


async def _roll_up(session: AsyncSession, transfer: Transfer, storage_id: str) -> None:
    day = transfer.submitted_at.date()
    bucket = await session.scalar(
        sa.select(TransferStat).where(
            TransferStat.day == day,
            TransferStat.user == transfer.user,
            TransferStat.storage_id == storage_id,
            TransferStat.route == transfer.route,
            TransferStat.kind == transfer.kind,
        )
    )
    if bucket is None:
        bucket = TransferStat(
            day=day,
            user=transfer.user,
            storage_id=storage_id,
            route=transfer.route,
            kind=transfer.kind,
            transfers=0,
            succeeded=0,
            bytes_transferred=0,
            queue_wait_seconds=0,
            running_seconds=0,
        )
        session.add(bucket)
    bucket.transfers += 1
    bucket.succeeded += 1 if transfer.state is TransferState.SUCCEEDED else 0
    bucket.bytes_transferred += transfer.bytes_done
    if transfer.started_at is not None:
        bucket.queue_wait_seconds += int(
            (transfer.started_at - transfer.submitted_at).total_seconds()
        )
        if transfer.finished_at is not None:
            bucket.running_seconds += int(
                (transfer.finished_at - transfer.started_at).total_seconds()
            )
    await session.flush()
