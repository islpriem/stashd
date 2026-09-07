"""Queue and transfer history reads."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from stashd.api.deps import (
    Caller,
    Cluster,
    Session,
    Store,
    Ticking,
    caller_is_admin,
    subject_owner,
    transfer_dispatcher,
)
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Transfer as TransferRow
from stashd.schemas.transfers import (
    Preflight,
    Submit,
    SubmitWarm,
    Transfer,
    Transfers,
)
from stashd.services import cancel, warm
from stashd.services import transfers as service
from stashd.services import transfers_release as release

router = APIRouter()


def to_wire(row: TransferRow) -> Transfer:
    return Transfer(
        id=row.id,
        kind=row.kind,
        user=row.user,
        fileset_id=row.fileset_id,
        peer_ref=row.peer_ref,
        state=row.state,
        route=row.route,
        bytes_total=row.bytes_total,
        bytes_done=row.bytes_done,
        files_total=row.files_total,
        files_done=row.files_done,
        executing_daemon_id=row.executing_daemon_id,
        bwlimit_bytes_per_s=row.bwlimit_bytes_per_s,
        attempt=row.attempt,
        error_code=row.error_code,
        error_detail=row.error_detail,
        submitted_at=row.submitted_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/transfers")
async def list_transfers(
    session: Session,
    user: Annotated[str | None, Query()] = None,
    state: Annotated[TransferState | None, Query()] = None,
    kind: Annotated[TransferKind | None, Query()] = None,
    storage: Annotated[str | None, Query()] = None,
    fileset_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(gt=0, le=service.MAX_PAGE)] = 50,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> Transfers:
    rows, next_cursor = await service.list_transfers(
        session,
        user=user,
        state=state,
        kind=kind,
        storage=storage,
        fileset_id=fileset_id,
        limit=limit,
        cursor=str(cursor) if cursor is not None else None,
    )
    return Transfers(transfers=[to_wire(row) for row in rows], next_cursor=next_cursor)


@router.get("/transfers/{transfer_id}")
async def get_transfer(session: Session, transfer_id: int) -> Transfer:
    return to_wire(await service.get_transfer(session, transfer_id))


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
async def submit_transfer(
    request: Request,
    caller: Caller,
    session: Session,
    cluster: Cluster,
    store: Store,
    clock: Ticking,
    body: Submit,
) -> Transfer | Preflight:
    """Submit a warm or a release. A release runs synchronously."""
    if isinstance(body, SubmitWarm):
        return await _warm(request, caller, session, cluster, clock, body)
    released = await release.release_fileset(
        session,
        store=store,
        clock=clock,
        actor=caller,
        is_admin=caller_is_admin(caller, cluster),
        subject_user=body.user or caller.username,
        storage_id=body.target.storage,
        name=body.target.fileset,
    )
    return to_wire(released)


async def _warm(
    request: Request,
    caller: Caller,
    session: Session,
    cluster: Cluster,
    clock: Ticking,
    body: SubmitWarm,
) -> Transfer | Preflight:
    owner = subject_owner(request, caller, body.user)
    dispatcher = transfer_dispatcher(request)
    plan = await warm.preflight(
        session,
        cluster=cluster,
        dispatcher=dispatcher,
        owner=owner,
        source_storage_id=body.source.storage,
        source_path=body.source.path,
        target_storage_id=body.target.storage,
        name=body.target.fileset,
        size_bytes=body.size_bytes,
        refresh=body.refresh,
    )
    start, duration = warm.eta(cluster, plan)
    if body.dry_run:
        return Preflight(
            kind=TransferKind.WARM,
            source=plan.source_reference,
            target=plan.target_reference,
            path=plan.path,
            route=str(plan.route),
            bytes_total=plan.bytes_total,
            file_count=plan.file_count,
            allocation_bytes=plan.allocation_bytes,
            refresh=plan.refresh,
            estimated_start_seconds=start,
            estimated_duration_seconds=duration,
        )
    submitted = await warm.submit_warm(
        session,
        cluster=cluster,
        dispatcher=dispatcher,
        clock=clock,
        actor=caller,
        owner=owner,
        plan=plan,
        source_storage_id=body.source.storage,
        source_path=body.source.path,
        target_storage_id=body.target.storage,
        name=body.target.fileset,
    )
    return to_wire(submitted)


@router.delete("/transfers/{transfer_id}")
async def cancel_transfer(
    caller: Caller,
    session: Session,
    cluster: Cluster,
    clock: Ticking,
    request: Request,
    transfer_id: int,
) -> Transfer:
    """Stop a transfer. What already arrived stays, for its owner to deal with."""
    cancelled = await cancel.cancel_transfer(
        session,
        cluster=cluster,
        dispatcher=transfer_dispatcher(request),
        clock=clock,
        actor=caller,
        is_admin=caller_is_admin(caller, cluster),
        transfer_id=transfer_id,
    )
    return to_wire(cancelled)
