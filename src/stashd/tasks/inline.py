"""A runner that does the work where it stands.

What a single-storage daemon without a broker uses, and what the tests run against. The
Celery runner has the same shape; only where the work happens differs.
"""

from dataclasses import dataclass, field
from itertools import count

from stashd.drivers.base import StorageDriver
from stashd.engines.base import TransferEngine, TransferOptions
from stashd.tasks.runner import TaskProgress, TransferTask


@dataclass
class InlineTaskRunner:
    drivers: dict[str, StorageDriver]
    engine: TransferEngine
    results: dict[str, TaskProgress] = field(default_factory=dict)
    _ids: "count[int]" = field(default_factory=lambda: count(1))

    def submit(self, task: TransferTask) -> str:
        task_id = f"inline-{next(self._ids)}"
        driver = self.drivers[task.storage_id]
        source = driver.endpoint(driver.resolve(task.source_path).absolute)
        result = self.engine.run(
            source,
            task.target,
            TransferOptions(bwlimit_bytes_per_s=task.bwlimit_bytes_per_s, delete=task.delete),
            owner=task.owner,
            handle=task_id,
        )
        self.results[task_id] = TaskProgress(
            task_id=task_id,
            transfer_id=task.transfer_id,
            state="SUCCEEDED" if result.ok else "FAILED",
            bytes_done=result.bytes_transferred,
            files_done=result.files_transferred,
            failure=result.failure,
            message=result.message,
        )
        return task_id

    def progress(self, task_id: str) -> TaskProgress | None:
        return self.results.get(task_id)

    def abort(self, task_id: str) -> bool:
        if task_id not in self.results:
            return False
        return self.engine.cancel(task_id)
