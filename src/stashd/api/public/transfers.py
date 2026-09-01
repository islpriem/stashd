"""Queue and transfer history reads."""

from typing import Annotated

from fastapi import APIRouter, Query, status

from stashd.api.deps import Caller, Cluster, Session, Store, Ticking, caller_is_admin
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Transfer as TransferRow
from stashd.schemas.transfers import SubmitRelease, Transfer, Transfers
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
    caller: Caller,
    session: Session,
    cluster: Cluster,
    store: Store,
    clock: Ticking,
    body: SubmitRelease,
) -> Transfer:
    """Submit a transfer. Only `release` exists so far; it runs synchronously."""
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
