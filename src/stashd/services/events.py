"""Applying what a daemon reports about a transfer.

Events arrive duplicated and out of order. The sequence number decides: anything not
newer than what was applied is acknowledged and ignored, and no transition ever moves a
transfer backwards.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import NotFound
from stashd.domain.failures import FailureClass
from stashd.domain.fairshare import points_for_bytes
from stashd.domain.filesets import FilesetState, next_fileset_state
from stashd.domain.transfers import (
    TransferState,
    is_backwards,
    next_transfer_state,
    should_retry,
)
from stashd.models import Fileset, Transfer
from stashd.services import fairshare

logger = structlog.get_logger()

STARTED = "started"
PROGRESS = "progress"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"

_TARGET_STATE = {
    STARTED: TransferState.RUNNING,
    PROGRESS: TransferState.RUNNING,
    FINISHED: TransferState.SUCCEEDED,
    FAILED: TransferState.FAILED,
}


@dataclass(frozen=True, slots=True)
class Event:
    transfer_id: int
    sequence: int
    kind: str
    daemon_id: str
    at: datetime
    bytes_done: int = 0
    files_done: int = 0
    failure: str | None = None
    message: str = ""


async def apply_event(
    session: AsyncSession, clock: Clock, event: Event, cluster: ClusterConfig
) -> tuple[bool, Transfer]:
    transfer = await session.get(Transfer, event.transfer_id)
    if transfer is None:
        raise NotFound(
            f"no transfer with id {event.transfer_id}", transfer_id=event.transfer_id
        )
    if event.sequence <= transfer.last_sequence:
        logger.info(
            "event.ignored",
            transfer_id=transfer.id,
            sequence=event.sequence,
            applied=transfer.last_sequence,
        )
        return False, transfer

    target = _TARGET_STATE[event.kind]
    if is_backwards(transfer.state, target) and transfer.state is not target:
        logger.info("event.backwards", transfer_id=transfer.id, state=str(transfer.state))
        return False, transfer

    transfer.last_sequence = event.sequence
    if transfer.state is not target:
        transfer.state = next_transfer_state(transfer.state, target)
    if event.kind == STARTED:
        transfer.started_at = transfer.started_at or event.at
        transfer.executing_daemon_id = event.daemon_id
    if event.bytes_done:
        transfer.bytes_done = min(event.bytes_done, transfer.bytes_total or event.bytes_done)
    if event.files_done:
        transfer.files_done = event.files_done
    if event.kind in (FINISHED, FAILED):
        transfer.finished_at = event.at
    if event.kind == FAILED:
        transfer.error_code = event.failure
        transfer.error_detail = event.message or None

    retrying = event.kind == FAILED and _retry(cluster, transfer, event)
    if retrying:
        _requeue(clock, cluster, transfer)
    await _apply_to_fileset(session, clock, transfer, event, retrying=retrying)
    if event.kind in (FAILED, CANCELLED) and not retrying:
        await _refund(session, clock, cluster, transfer)
    await session.commit()
    return True, transfer


def _retry(cluster: ClusterConfig, transfer: Transfer, event: Event) -> bool:
    """Only the classes the config names, and only while attempts are left."""
    try:
        failure = FailureClass(str(event.failure))
    except ValueError:
        return False
    return should_retry(
        failure,
        attempt=transfer.attempt,
        count=cluster.transfer.retries.count,
        retry_on=frozenset(cluster.transfer.retries.retry_on),
    )


def _requeue(clock: Clock, cluster: ClusterConfig, transfer: Transfer) -> None:
    """Back into the queue, after a wait, keeping the place it had."""
    transfer.state = next_transfer_state(transfer.state, TransferState.SUBMITTED)
    transfer.attempt += 1
    transfer.retry_after = clock.now() + cluster.transfer.retries.backoff
    transfer.finished_at = None
    transfer.task_id = None
    logger.info("transfer.retrying", transfer_id=transfer.id, attempt=transfer.attempt)


async def _refund(
    session: AsyncSession, clock: Clock, cluster: ClusterConfig, transfer: Transfer
) -> None:
    """Refunded only when it never ran: 'on cancel or failure before RUNNING'."""
    if transfer.started_at is not None:
        return
    await fairshare.charge(
        session,
        transfer.user,
        -points_for_bytes(
            transfer.kind,
            transfer.bytes_total,
            points_per_gib=cluster.scheduling.fairshare.points_per_gib,
        ),
        clock,
        cluster.scheduling.fairshare.half_life,
    )


async def _apply_to_fileset(
    session: AsyncSession, clock: Clock, transfer: Transfer, event: Event, *, retrying: bool
) -> None:
    fileset = await session.get(Fileset, transfer.fileset_id)
    if fileset is None:  # pragma: no cover - a transfer always has one
        return
    fileset.last_transfer_id = transfer.id
    if event.kind == FINISHED:
        fileset.state = next_fileset_state(fileset.state, FilesetState.READY)
        fileset.warm_finished_at = event.at
        fileset.used_bytes = transfer.bytes_done
        fileset.used_bytes_at = clock.now()
        fileset.file_count = transfer.files_done or fileset.file_count
    elif event.kind == FAILED and not retrying:
        # The reservation stays until the owner releases the fileset.
        fileset.state = next_fileset_state(fileset.state, FilesetState.FAILED)
