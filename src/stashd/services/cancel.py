"""Cancelling a transfer.

Queued work simply stops being offered; work a daemon has is killed there. Either way the
fileset is left FAILED with whatever arrived: the partial data is the owner's to release
or to fill again, and the reservation stays until they do.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import ErrorCode, NotFound, StashError
from stashd.domain.failures import FailureClass
from stashd.domain.fairshare import points_for_bytes
from stashd.domain.filesets import FilesetState, holds_allocation, next_fileset_state
from stashd.domain.identity import Principal
from stashd.domain.transfers import TransferState, can_cancel, next_transfer_state
from stashd.models import Fileset, Transfer
from stashd.services import audit, fairshare
from stashd.services.filesets import Forbidden

logger = structlog.get_logger()


class Conflict(StashError):
    code = ErrorCode.CONFLICT


async def cancel_transfer(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    clock: Clock,
    actor: Principal,
    is_admin: bool,
    transfer_id: int,
) -> Transfer:
    transfer = await session.get(Transfer, transfer_id)
    if transfer is None:
        raise NotFound(f"no transfer with id {transfer_id}", transfer_id=transfer_id)
    if transfer.user != actor.username and not is_admin:
        raise Forbidden(
            f"transfer {transfer_id} belongs to {transfer.user}", user=transfer.user
        )
    if not can_cancel(transfer.state):
        raise Conflict(
            f"transfer {transfer_id} is {transfer.state} and cannot be cancelled",
            state=str(transfer.state),
        )

    if transfer.task_id is not None:
        await _stop_on_daemon(dispatcher, transfer)

    transfer.state = next_transfer_state(transfer.state, TransferState.CANCELLED)
    transfer.finished_at = clock.now()
    transfer.error_code = str(FailureClass.CANCELLED)
    await _leave_fileset_failed(session, transfer)
    if transfer.started_at is None:
        # It never ran, so it costs nothing.
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
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=transfer.user,
        object_type="transfer",
        object_id=str(transfer.id),
        action="cancel",
    )
    await session.commit()
    return transfer


async def _stop_on_daemon(dispatcher: TransferDispatcher, transfer: Transfer) -> None:
    """A daemon that cannot be reached must not leave the controller believing it runs."""
    source_storage = transfer.route.split("->", 1)[0]
    try:
        await dispatcher.abort(source_storage, transfer.task_id or "")
    except StashError as unreachable:
        logger.warning(
            "cancel.daemon_unreachable", transfer_id=transfer.id, reason=str(unreachable)
        )


async def _leave_fileset_failed(session: AsyncSession, transfer: Transfer) -> None:
    fileset = await session.get(Fileset, transfer.fileset_id)
    if fileset is None or not holds_allocation(fileset.state):  # pragma: no cover - always one
        return
    if fileset.state is not FilesetState.FAILED:
        fileset.state = next_fileset_state(fileset.state, FilesetState.FAILED)
    fileset.last_transfer_id = transfer.id
