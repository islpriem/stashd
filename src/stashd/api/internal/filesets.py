"""Fileset directories, on the daemon that owns the filesystem.

The controller never touches user data: it asks here, and every path is re-derived from
the storage's own configuration rather than taken from the request.
"""

import structlog
from fastapi import APIRouter, Request, Response, status

from stashd.domain.errors import NotFound
from stashd.domain.storage import FilesetLocation as DomainLocation
from stashd.domain.storage import Owner as DomainOwner
from stashd.drivers.base import StorageDriver
from stashd.schemas.internal import (
    CreateFileset,
    DeleteFileset,
    FilesetLocation,
    FilesetUsage,
    FilesetUsages,
    MeasureFilesets,
    SetQuota,
    StorageUsage,
)

router = APIRouter()


def driver_for(request: Request, storage_id: str) -> StorageDriver:
    drivers: dict[str, StorageDriver] = request.app.state.drivers or {}
    driver = drivers.get(storage_id)
    if driver is None:
        raise NotFound(f"this daemon does not serve storage {storage_id}", storage=storage_id)
    return driver


@router.post("/filesets", status_code=status.HTTP_201_CREATED)
async def create_fileset(request: Request, body: CreateFileset) -> FilesetLocation:
    driver = driver_for(request, body.storage_id)
    owner = DomainOwner(user=body.owner.user, uid=body.owner.uid, gid=body.owner.gid)
    location = driver.create_fileset(owner, body.name, body.allocation_bytes)
    driver.set_fileset_quota(location, body.allocation_bytes)
    return FilesetLocation(
        storage_id=location.storage_id, name=location.name, path=location.path
    )


@router.post("/filesets/quota", status_code=status.HTTP_204_NO_CONTENT)
async def set_quota(request: Request, body: SetQuota) -> Response:
    """Apply an allocation to the storage, where the driver can enforce one."""
    driver = driver_for(request, body.storage_id)
    owner = DomainOwner(user=body.owner.user, uid=body.owner.uid, gid=body.owner.gid)
    driver.set_fileset_quota(
        DomainLocation(storage_id=body.storage_id, name=body.name, owner=owner, path=body.path),
        body.allocation_bytes,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/filesets/{fileset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fileset(request: Request, fileset_id: int, body: DeleteFileset) -> Response:
    structlog.contextvars.bind_contextvars(fileset_id=fileset_id)
    driver = driver_for(request, body.storage_id)
    owner = DomainOwner(user=body.owner.user, uid=body.owner.uid, gid=body.owner.gid)
    driver.delete_fileset(
        DomainLocation(storage_id=body.storage_id, name=body.name, owner=owner, path=body.path)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/filesets/usage")
async def fileset_usage(request: Request, body: MeasureFilesets) -> FilesetUsages:
    """What each fileset actually holds, measured as its owner."""
    driver = driver_for(request, body.storage_id)
    measured = []
    for wanted in body.filesets:
        location = DomainLocation(
            storage_id=body.storage_id,
            name=wanted.name,
            owner=DomainOwner(
                user=wanted.owner.user, uid=wanted.owner.uid, gid=wanted.owner.gid
            ),
            path=wanted.path,
        )
        usage = driver.fileset_usage(location)
        measured.append(
            FilesetUsage(
                fileset_id=wanted.fileset_id,
                used_bytes=usage.used_bytes,
                file_count=usage.file_count,
            )
        )
    return FilesetUsages(filesets=measured)


@router.get("/storages/{storage_id}/usage")
async def storage_usage(request: Request, storage_id: str) -> StorageUsage:
    """Filesystem truth for the whole storage, for the metric that watches it."""
    usage = driver_for(request, storage_id).storage_usage()
    return StorageUsage(
        storage_id=storage_id, used_bytes=usage.used_bytes, file_count=usage.file_count
    )
