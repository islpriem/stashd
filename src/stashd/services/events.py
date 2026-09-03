"""Applying what a daemon reports about a transfer.

Events arrive duplicated and out of order. The sequence number decides: anything not
newer than what was applied is acknowledged and ignored, and no transition ever moves a
transfer backwards.
"""

from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.clock import Clock
from stashd.domain.errors import NotFound
from stashd.domain.filesets import FilesetState, next_fileset_state
from stashd.domain.transfers import TransferState, is_backwards, next_transfer_state
from stashd.models import Fileset, Transfer

logger = structlog.get_logger()

STARTED = "started"
PROGRESS = "progress"
FINISHED = "finished"
FAILED = "failed"

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
    session: AsyncSession, clock: Clock, event: Event
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

    await _apply_to_fileset(session, clock, transfer, event)
    await session.commit()
    return True, transfer


async def _apply_to_fileset(
    session: AsyncSession, clock: Clock, transfer: Transfer, event: Event
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
    elif event.kind == FAILED:
        # The reservation stays until the owner releases the fileset.
        fileset.state = next_fileset_state(fileset.state, FilesetState.FAILED)
