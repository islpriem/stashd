"""Running one transfer, wherever the runner decided to run it."""

import structlog

from stashd.drivers.base import StorageDriver
from stashd.engines.base import Progress, TransferEngine, TransferOptions
from stashd.tasks.runner import TaskProgress, TransferTask

logger = structlog.get_logger()


def execute_transfer(
    drivers: dict[str, StorageDriver],
    engine: TransferEngine,
    task: TransferTask,
    task_id: str,
    on_progress: object = None,
) -> TaskProgress:
    """Resolve the source here, move the data, and say what happened."""
    driver = drivers[task.storage_id]
    source = driver.endpoint(driver.resolve(task.source_path).absolute)
    callback = on_progress if callable(on_progress) else None
    result = engine.run(
        source,
        task.target,
        TransferOptions(bwlimit_bytes_per_s=task.bwlimit_bytes_per_s, delete=task.delete),
        owner=task.owner,
        handle=task_id,
        on_progress=callback,
    )
    logger.info(
        "transfer.finished",
        task_id=task_id,
        transfer_id=task.transfer_id,
        failure=str(result.failure) if result.failure else None,
        bytes=result.bytes_transferred,
    )
    return TaskProgress(
        task_id=task_id,
        transfer_id=task.transfer_id,
        state="SUCCEEDED" if result.ok else "FAILED",
        bytes_done=result.bytes_transferred,
        files_done=result.files_transferred,
        failure=result.failure,
        message=result.message,
    )


def progress_reporter(reporter: object, task: TransferTask) -> object:
    def report(progress: Progress) -> None:
        reporter.report(task.transfer_id, "progress", bytes_done=progress.bytes_done)  # type: ignore[attr-defined]

    return report
