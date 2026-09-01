"""Fileset reads. Everyone may read every fileset."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from stashd.api.deps import Caller, Cluster, Session, Store, Ticking, subject_owner
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.models import Fileset as FilesetRow
from stashd.schemas.filesets import CreateFileset, Fileset, Filesets
from stashd.services import filesets as service

router = APIRouter()


def to_wire(row: FilesetRow) -> Fileset:
    source = (
        f"{row.source_storage_id}:{row.source_path}"
        if row.source_storage_id and row.source_path
        else None
    )
    return Fileset(
        id=row.id,
        name=row.name,
        reference=f"{row.storage_id}:{row.name}",
        owner_user=row.owner_user,
        storage_id=row.storage_id,
        kind=row.kind,
        state=row.state,
        path=row.path,
        allocated_bytes=row.allocated_bytes,
        used_bytes=row.used_bytes,
        used_bytes_at=row.used_bytes_at,
        file_count=row.file_count,
        over_allocation=row.over_allocation,
        source=source,
        created_at=row.created_at,
        warm_started_at=row.warm_started_at,
        warm_finished_at=row.warm_finished_at,
        last_flushed_at=row.last_flushed_at,
        last_flush_target=row.last_flush_target,
        released_at=row.released_at,
        last_transfer_id=row.last_transfer_id,
    )


@router.get("/filesets")
async def list_filesets(
    session: Session,
    storage: Annotated[str | None, Query()] = None,
    user: Annotated[str | None, Query()] = None,
    name: Annotated[str | None, Query()] = None,
    kind: Annotated[FilesetKind | None, Query()] = None,
    state: Annotated[FilesetState | None, Query()] = None,
) -> Filesets:
    rows = await service.list_filesets(
        session, storage=storage, user=user, name=name, kind=kind, state=state
    )
    return Filesets(filesets=[to_wire(row) for row in rows])


@router.get("/filesets/{fileset_id}")
async def get_fileset(session: Session, fileset_id: int) -> Fileset:
    return to_wire(await service.get_fileset(session, fileset_id))


@router.post("/filesets", status_code=status.HTTP_201_CREATED)
async def create_fileset(
    request: Request,
    caller: Caller,
    session: Session,
    cluster: Cluster,
    store: Store,
    clock: Ticking,
    body: CreateFileset,
) -> Fileset:
    owner = subject_owner(request, caller, body.user)
    created = await service.create_output_fileset(
        session,
        cluster=cluster,
        store=store,
        clock=clock,
        actor=caller,
        owner=owner,
        storage_id=body.storage,
        name=body.name,
        size_bytes=body.size_bytes,
    )
    return to_wire(created)
