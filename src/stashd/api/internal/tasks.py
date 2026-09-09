"""Probing, preparing and running a transfer, on the daemon that owns the storage.

Every path the controller sends is resolved and re-checked here, as the requesting user:
what the controller decided from a string is never trusted.
"""

import structlog
from fastapi import APIRouter, Request, status

from stashd.api.internal.filesets import driver_for
from stashd.domain.errors import NotFound
from stashd.domain.storage import Owner as DomainOwner
from stashd.engines.base import TransferEndpoint
from stashd.schemas.internal import (
    Endpoint,
    Prepare,
    Probe,
    ProbeResult,
    StartTask,
    TaskState,
)
from stashd.tasks.runner import TaskRunner, TransferTask

router = APIRouter()
logger = structlog.get_logger()

PROBE_TIMEOUT = 60.0


def owner_of(owner: object) -> DomainOwner:
    return DomainOwner(user=owner.user, uid=owner.uid, gid=owner.gid)  # type: ignore[attr-defined]


def runner_of(request: Request) -> TaskRunner:
    runner: TaskRunner | None = request.app.state.runner
    if runner is None:
        raise NotFound("this daemon runs no transfers")
    return runner


@router.post("/probe")
async def probe(request: Request, body: Probe) -> ProbeResult:
    driver = driver_for(request, body.storage_id)
    owner = owner_of(body.owner)
    resolved = driver.resolve(body.path)
    found = driver.stat(resolved, owner=owner)
    if not found.exists or not found.readable:
        return ProbeResult(
            path=resolved.absolute,
            exists=found.exists,
            is_dir=found.is_dir,
            readable=found.readable,
            writable=found.writable,
            bytes_total=0,
            file_count=0,
            complete=True,
        )
    measured = driver.measure(resolved, owner=owner, timeout=PROBE_TIMEOUT)
    return ProbeResult(
        path=resolved.absolute,
        exists=True,
        is_dir=found.is_dir,
        readable=True,
        writable=found.writable,
        bytes_total=measured.bytes_total,
        file_count=measured.file_count,
        complete=measured.complete,
    )


@router.post("/prepare")
async def prepare(request: Request, body: Prepare) -> Endpoint:
    """Create the destination and say how to reach it. Safe to call again."""
    driver = driver_for(request, body.storage_id)
    owner = owner_of(body.owner)
    location = driver.create_fileset(owner, body.name, body.allocation_bytes)
    driver.set_fileset_quota(location, body.allocation_bytes)
    endpoint = driver.endpoint(location.path)
    return Endpoint(path=endpoint.path, host=request.app.state.bootstrap.node.daemon_id)


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def start_task(request: Request, body: StartTask) -> TaskState:
    # Refuse a path that could leave the storage before anything is queued.
    driver_for(request, body.storage_id).resolve(body.source_path)
    task_id = runner_of(request).submit(
        TransferTask(
            transfer_id=body.transfer_id,
            storage_id=body.storage_id,
            source_path=body.source_path,
            owner=owner_of(body.owner),
            target=TransferEndpoint(
                path=body.target.path, host=body.target.host, user=body.target.user
            ),
            bwlimit_bytes_per_s=body.bwlimit_bytes_per_s,
            delete=body.delete,
        )
    )
    logger.info("task.submitted", task_id=task_id, transfer_id=body.transfer_id)
    return TaskState(task_id=task_id, transfer_id=body.transfer_id, state="SUBMITTED")


@router.get("/tasks/{task_id}")
async def task_state(request: Request, task_id: str) -> TaskState:
    found = runner_of(request).progress(task_id)
    if found is None:
        raise NotFound(f"no task {task_id} on this daemon", task_id=task_id)
    return TaskState(
        task_id=found.task_id,
        transfer_id=found.transfer_id,
        state=found.state,
        bytes_done=found.bytes_done,
        files_done=found.files_done,
        failure=str(found.failure) if found.failure else None,
        message=found.message,
    )


@router.delete("/tasks/{task_id}")
async def abort_task(request: Request, task_id: str) -> TaskState:
    """Cancel leaves the partial data where it is; the fileset is released later."""
    runner = runner_of(request)
    if not runner.abort(task_id):
        raise NotFound(f"no task {task_id} to cancel on this daemon", task_id=task_id)
    found = runner.progress(task_id)
    return TaskState(
        task_id=task_id,
        transfer_id=found.transfer_id if found else 0,
        state="CANCELLED",
    )
