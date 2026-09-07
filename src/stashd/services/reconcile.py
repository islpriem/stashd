"""What the controller believes after a restart.

Every transfer it thinks is in flight is checked against the daemon that was running it.
A daemon that cannot be asked is given time before its work is given up on: a restart
must not lose transfers, and must not invent failures either.
"""

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import StashError
from stashd.domain.transfers import TransferState, next_transfer_state
from stashd.models import Transfer
from stashd.services.events import Event, apply_event

logger = structlog.get_logger()

DAEMON_UNREACHABLE = "daemon_unreachable"
FINISHED = {"SUCCEEDED": "finished", "FAILED": "failed", "CANCELLED": "failed"}


async def reconcile(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    clock: Clock,
) -> int:
    """Bring every in-flight transfer up to date. Returns how many changed."""
    rows = await session.scalars(
        sa.select(Transfer).where(
            Transfer.state.in_([TransferState.ASSIGNED, TransferState.RUNNING])
        )
    )
    changed = 0
    for transfer in list(rows):
        if await _reconcile_one(session, cluster, dispatcher, clock, transfer):
            changed += 1
    return changed


async def _reconcile_one(
    session: AsyncSession,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    clock: Clock,
    transfer: Transfer,
) -> bool:
    if transfer.task_id is None:
        # It was never handed over, so it can simply be offered again.
        transfer.state = next_transfer_state(transfer.state, TransferState.SUBMITTED)
        await session.commit()
        logger.info("reconcile.requeued", transfer_id=transfer.id)
        return True

    source_storage = transfer.route.split("->", 1)[0]
    try:
        found = await dispatcher.task_state(source_storage, transfer.task_id)
    except StashError as unreachable:
        return await _give_up_or_wait(session, cluster, clock, transfer, unreachable)

    kind = FINISHED.get(found.state)
    if kind is None:
        return False
    await apply_event(
        session,
        clock,
        Event(
            transfer_id=transfer.id,
            sequence=transfer.last_sequence + 1,
            kind=kind,
            daemon_id=transfer.executing_daemon_id or "",
            at=clock.now(),
            bytes_done=found.bytes_done,
            files_done=found.files_done,
            failure=found.failure,
            message=found.message,
        ),
        cluster,
    )
    logger.info("reconcile.adopted", transfer_id=transfer.id, state=found.state)
    return True


async def _give_up_or_wait(
    session: AsyncSession,
    cluster: ClusterConfig,
    clock: Clock,
    transfer: Transfer,
    reason: StashError,
) -> bool:
    """A daemon may be restarting; only give up once the grace period has run out."""
    since = transfer.started_at or transfer.submitted_at
    if clock.now() - since < cluster.timeouts.daemon_unreachable:
        logger.info("reconcile.waiting", transfer_id=transfer.id, reason=str(reason))
        return False
    await apply_event(
        session,
        clock,
        Event(
            transfer_id=transfer.id,
            sequence=transfer.last_sequence + 1,
            kind="failed",
            daemon_id=transfer.executing_daemon_id or "",
            at=clock.now(),
            failure=DAEMON_UNREACHABLE,
            message=str(reason),
        ),
        cluster,
    )
    logger.warning("reconcile.gave_up", transfer_id=transfer.id, reason=str(reason))
    return True
