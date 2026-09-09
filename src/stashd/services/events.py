"""Applying what a daemon reports about a transfer.

Events arrive duplicated and out of order. The sequence number decides: anything not
newer than what was applied is acknowledged and ignored, and no transition ever moves a
transfer backwards.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.filesets import FilesetStore
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import NotFound
from stashd.domain.failures import FailureClass
from stashd.domain.fairshare import points_for_bytes
from stashd.domain.filesets import FilesetState, next_fileset_state
from stashd.domain.identity import Principal
from stashd.domain.transfers import (
    TransferKind,
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
    session: AsyncSession,
    clock: Clock,
    event: Event,
    cluster: ClusterConfig,
    store: FilesetStore | None = None,
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
    fileset = await _apply_to_fileset(session, clock, transfer, event, retrying=retrying)
    if event.kind in (FAILED, CANCELLED) and not retrying:
        await _refund(session, clock, cluster, transfer)
    await session.commit()
    if fileset is not None and store is not None and _releases_now(transfer, event):
        await _release_after_flush(session, clock, transfer, fileset, store)
    return True, transfer


def _releases_now(transfer: Transfer, event: Event) -> bool:
    """A flush that asked for it releases the fileset it wrote out."""
    return (
        transfer.kind is TransferKind.FLUSH
        and event.kind == FINISHED
        and transfer.release_after
    )


async def _release_after_flush(
    session: AsyncSession,
    clock: Clock,
    transfer: Transfer,
    fileset: Fileset,
    store: FilesetStore,
) -> None:
    from stashd.services.transfers_release import perform_release

    owner = Principal(uid=fileset.owner_uid, gid=fileset.owner_gid, username=fileset.owner_user)
    try:
        await perform_release(session, store=store, clock=clock, actor=owner, fileset=fileset)
    except Exception as failure:  # the flush succeeded; the release is its own outcome
        logger.warning(
            "flush.release_failed",
            transfer_id=transfer.id,
            fileset_id=fileset.id,
            error=str(failure),
        )


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
) -> Fileset | None:
    fileset = await session.get(Fileset, transfer.fileset_id)
    if fileset is None:  # pragma: no cover - a transfer always has one
        return None
    fileset.last_transfer_id = transfer.id
    flushing = transfer.kind is TransferKind.FLUSH
    if event.kind == FINISHED:
        fileset.state = next_fileset_state(fileset.state, FilesetState.READY)
        if flushing:
            # A flush reads the fileset out; what it holds is unchanged.
            fileset.last_flushed_at = event.at
            fileset.last_flush_target = transfer.peer_ref
        else:
            fileset.warm_finished_at = event.at
            fileset.used_bytes = transfer.bytes_done
            fileset.used_bytes_at = clock.now()
            fileset.file_count = transfer.files_done or fileset.file_count
    elif event.kind == FAILED and not retrying:
        # The reservation stays until the owner releases the fileset.
        fileset.state = next_fileset_state(fileset.state, FilesetState.FAILED)
    return fileset
