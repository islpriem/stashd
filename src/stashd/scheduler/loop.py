"""One pass of the scheduler.

Guarded by an advisory lock so two controllers, or two overlapping passes, cannot
dispatch the same transfer twice. The ordering and the limits are the domain's;
this module only gathers what they need and carries out what they decided.
"""

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import StashError
from stashd.domain.priority import DecayingFairShare, PriorityPolicy, order_queue
from stashd.domain.routes import Channel, Route, channel_for
from stashd.domain.scheduling import (
    BandwidthLimits,
    ConcurrencyLimits,
    Dispatch,
    Load,
    QueuedTransfer,
    plan_dispatch,
)
from stashd.domain.storage import Owner
from stashd.domain.transfers import TransferKind, TransferState, next_transfer_state
from stashd.engines.base import TransferEndpoint
from stashd.models import Fileset, Transfer
from stashd.services import drain, fairshare

logger = structlog.get_logger()

# One well-known key: whoever holds it is the scheduler for this pass.
SCHEDULER_LOCK = 0x57A5_4EDD


async def schedule_once(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    clock: Clock,
    policy: PriorityPolicy | None = None,
) -> list[Dispatch]:
    """Dispatch what fits. Returns what was started, which is empty when nothing does."""
    if not await _take_lock(session):
        logger.debug("scheduler.busy")
        return []

    waiting = await _waiting(session, clock)
    if not waiting:
        await session.rollback()
        return []

    half_life = cluster.scheduling.fairshare.half_life
    accounts = await fairshare.accounts_for(
        session, sorted({row.user for row in waiting}), clock, half_life
    )
    ordered = order_queue(
        [_queued(row) for row in waiting],
        accounts,
        policy or DecayingFairShare(half_life),
        clock.now(),
    )
    plan = plan_dispatch(
        ordered,
        await _load(session),
        _concurrency(cluster),
        _bandwidth(cluster),
        drained=await drain.drained_storages(session),
    )

    started: list[Dispatch] = []
    by_id = {row.id: row for row in waiting}
    for dispatch in plan:
        transfer = by_id[dispatch.transfer_id]
        if not transfer.kind.moves_data:
            continue  # a release moves nothing; it is carried out where it was asked for
        if await _start(session, dispatcher, transfer, dispatch, cluster):
            started.append(dispatch)
    await session.commit()
    return started


async def _take_lock(session: AsyncSession) -> bool:
    taken = await session.scalar(
        sa.text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SCHEDULER_LOCK}
    )
    return bool(taken)


async def _waiting(session: AsyncSession, clock: Clock) -> list[Transfer]:
    """What may be offered now: a retry waits out its backoff first."""
    rows = await session.scalars(
        sa.select(Transfer)
        .where(
            Transfer.state == TransferState.SUBMITTED,
            sa.or_(Transfer.retry_after.is_(None), Transfer.retry_after <= clock.now()),
        )
        .order_by(Transfer.id)
    )
    return list(rows)


def _queued(transfer: Transfer) -> QueuedTransfer:
    source, target = transfer.route.split("->", 1)
    return QueuedTransfer(
        id=transfer.id,
        user=transfer.user,
        kind=transfer.kind,
        route=Route(source, target),
        bytes_total=transfer.bytes_total,
        submitted_at=transfer.submitted_at,
    )


async def _load(session: AsyncSession) -> Load:
    """What is already running, by the scopes the limits are counted in."""
    rows = await session.scalars(
        sa.select(Transfer).where(
            Transfer.state.in_([TransferState.ASSIGNED, TransferState.RUNNING])
        )
    )
    load = Load()
    for transfer in rows:
        queued = _queued(transfer)
        load.total += 1
        load.per_user[transfer.user] = load.per_user.get(transfer.user, 0) + 1
        for storage in queued.storages:
            load.per_storage[storage] = load.per_storage.get(storage, 0) + 1
        if transfer.kind.moves_data:
            load.per_route[transfer.route] = load.per_route.get(transfer.route, 0) + 1
    return load


def _concurrency(cluster: ClusterConfig) -> ConcurrencyLimits:
    limits = cluster.limits.concurrency
    return ConcurrencyLimits(
        global_=limits.global_,
        per_storage=limits.per_storage,
        per_user=limits.per_user,
        per_route=limits.per_route,
    )


def _bandwidth(cluster: ClusterConfig) -> BandwidthLimits:
    bandwidth = cluster.limits.bandwidth
    return BandwidthLimits(
        per_route_aggregate=bandwidth.per_route_aggregate_bytes_per_s,
        max_per_transfer=bandwidth.max_per_transfer_bytes_per_s,
        min_per_transfer=bandwidth.min_per_transfer_bytes_per_s,
    )


async def _start(
    session: AsyncSession,
    dispatcher: TransferDispatcher,
    transfer: Transfer,
    dispatch: Dispatch,
    cluster: ClusterConfig,
) -> bool:
    """Hand one transfer to the daemon that owns its source. A refusal leaves it queued."""
    fileset = await session.get(Fileset, transfer.fileset_id)
    if fileset is None:  # pragma: no cover - a transfer always has one
        return False
    source_storage, _ = transfer.route.split("->", 1)
    source_path = (transfer.peer_ref or "").split(":", 1)[-1]
    owner = Owner(user=fileset.owner_user, uid=fileset.owner_uid, gid=fileset.owner_gid)
    target = _endpoint(cluster, source_storage, fileset, owner)
    try:
        started = await dispatcher.start(
            source_storage,
            transfer_id=transfer.id,
            source_path=source_path,
            owner=owner,
            target=target,
            bwlimit_bytes_per_s=dispatch.bwlimit_bytes_per_s,
            delete=transfer.kind is TransferKind.WARM
            and transfer.attempt > 0
            and _is_refresh(fileset),
        )
    except StashError as refusal:
        logger.warning(
            "dispatch.refused",
            transfer_id=transfer.id,
            code=str(refusal.code),
            reason=str(refusal),
        )
        return False
    transfer.state = next_transfer_state(transfer.state, TransferState.ASSIGNED)
    transfer.executing_daemon_id = started.daemon_id
    transfer.task_id = started.task_id
    transfer.bwlimit_bytes_per_s = dispatch.bwlimit_bytes_per_s
    logger.info("dispatch.started", transfer_id=transfer.id, daemon=started.daemon_id)
    return True


def _endpoint(
    cluster: ClusterConfig, source_storage: str, fileset: Fileset, owner: Owner
) -> TransferEndpoint:
    """A local path when one daemon owns both ends, an ssh destination otherwise.

    The connection is made as the owner, which is who the source side is already running
    as. A deployment that connects as a service account instead needs rsync's
    --rsync-path, which is the other half of that open question and is not implemented.
    """
    source_daemon = cluster.daemon_for(source_storage)
    target_daemon = cluster.daemon_for(fileset.storage_id)
    if channel_for(source_daemon.id, target_daemon.id) is Channel.LOCAL:
        return TransferEndpoint(path=fileset.path)
    return TransferEndpoint(path=fileset.path, host=target_daemon.host, user=owner.user)


def _is_refresh(fileset: Fileset) -> bool:
    """--delete belongs to a refresh only: a fileset that already held this source."""
    return fileset.warm_finished_at is not None
