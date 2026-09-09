"""Releasing a fileset.

A release is a transfer that moves no data. For now it runs synchronously; the
scheduler takes it over later. The allocation is freed only after the daemon confirms the
directory is gone.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.filesets import FilesetStore
from stashd.domain.clock import Clock
from stashd.domain.errors import ErrorCode, NotFound, StashError
from stashd.domain.filesets import FilesetKind, FilesetState, next_fileset_state
from stashd.domain.identity import Principal
from stashd.domain.transfers import TransferKind, TransferState, next_transfer_state
from stashd.models import Fileset, Transfer
from stashd.services import audit
from stashd.services.filesets import Forbidden, live_fileset, location_of


class FlushTargetRequired(StashError):
    code = ErrorCode.FLUSH_TARGET_REQUIRED


async def release_fileset(
    session: AsyncSession,
    *,
    store: FilesetStore,
    clock: Clock,
    actor: Principal,
    is_admin: bool,
    subject_user: str,
    storage_id: str,
    name: str,
    discard: bool = False,
) -> Transfer:
    if subject_user != actor.username and not is_admin:
        raise Forbidden(f"only an admin may act for {subject_user}", user=subject_user)
    fileset = await live_fileset(session, subject_user, storage_id, name)
    if fileset is None:
        raise NotFound(
            f"{subject_user} has no fileset {name} on {storage_id}",
            storage=storage_id,
            name=name,
        )
    if fileset.owner_user != actor.username and not is_admin:
        raise Forbidden(
            f"{fileset.name} on {storage_id} belongs to {fileset.owner_user}",
            owner=fileset.owner_user,
        )
    # An output fileset exists nowhere else: losing it needs to be deliberate, whoever
    # asks. A cached one is reconstructible from its source.
    if fileset.kind is FilesetKind.OUTPUT and not discard:
        raise FlushTargetRequired(
            f"{name} on {storage_id} holds the only copy of its data: "
            f"flush it somewhere first, or release it with discard",
            storage=storage_id,
            name=name,
        )

    return await perform_release(
        session, store=store, clock=clock, actor=actor, fileset=fileset
    )


async def perform_release(
    session: AsyncSession,
    *,
    store: FilesetStore,
    clock: Clock,
    actor: Principal,
    fileset: Fileset,
) -> Transfer:
    """Delete the directory, then free the reservation. Never the other way round."""
    transfer = _start(session, clock, fileset)
    fileset.state = next_fileset_state(fileset.state, FilesetState.RELEASING)
    await session.commit()

    try:
        await store.delete(fileset.id, location_of(fileset))
    except Exception as failure:
        _fail(clock, fileset, transfer, failure)
        audit.record(
            session,
            clock,
            actor=actor,
            subject_user=fileset.owner_user,
            object_type="fileset",
            object_id=str(fileset.id),
            action="release",
            result=str(getattr(failure, "code", ErrorCode.INTERNAL)),
        )
        await session.commit()
        raise

    fileset.state = next_fileset_state(fileset.state, FilesetState.RELEASED)
    fileset.released_at = clock.now()
    fileset.last_transfer_id = transfer.id
    transfer.state = next_transfer_state(transfer.state, TransferState.SUCCEEDED)
    transfer.finished_at = clock.now()
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=fileset.owner_user,
        object_type="fileset",
        object_id=str(fileset.id),
        action="release",
        detail={
            "storage": fileset.storage_id,
            "name": fileset.name,
            "freed_bytes": fileset.allocated_bytes,
        },
    )
    await session.commit()
    return transfer


def _start(session: AsyncSession, clock: Clock, fileset: Fileset) -> Transfer:
    now = clock.now()
    transfer = Transfer(
        kind=TransferKind.RELEASE,
        user=fileset.owner_user,
        fileset_id=fileset.id,
        state=TransferState.SUBMITTED,
        route=f"{fileset.storage_id}->{fileset.storage_id}",
        submitted_at=now,
    )
    session.add(transfer)
    transfer.state = next_transfer_state(transfer.state, TransferState.ASSIGNED)
    transfer.state = next_transfer_state(transfer.state, TransferState.RUNNING)
    transfer.started_at = now
    return transfer


def _fail(clock: Clock, fileset: Fileset, transfer: Transfer, failure: Exception) -> None:
    """The reservation stays until the owner releases the fileset again."""
    fileset.state = next_fileset_state(fileset.state, FilesetState.FAILED)
    transfer.state = next_transfer_state(transfer.state, TransferState.FAILED)
    transfer.finished_at = clock.now()
    transfer.error_code = str(getattr(failure, "code", ErrorCode.INTERNAL))
    transfer.error_detail = str(failure)[:500] if isinstance(failure, StashError) else None
