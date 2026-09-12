"""A runner that does the work where it stands.

What the tests run against. The thread-pool runner a daemon uses has the same shape;
only where the work happens differs.
"""

from dataclasses import dataclass, field
from itertools import count

from stashd.drivers.base import StorageDriver
from stashd.engines.base import TransferEngine
from stashd.tasks.execute import execute_transfer
from stashd.tasks.runner import TaskProgress, TransferTask


@dataclass
class InlineTaskRunner:
    drivers: dict[str, StorageDriver]
    engine: TransferEngine
    results: dict[str, TaskProgress] = field(default_factory=dict)
    _ids: "count[int]" = field(default_factory=lambda: count(1))

    def submit(self, task: TransferTask) -> str:
        task_id = f"inline-{next(self._ids)}"
        self.results[task_id] = execute_transfer(self.drivers, self.engine, task, task_id)
        return task_id

    def progress(self, task_id: str) -> TaskProgress | None:
        return self.results.get(task_id)

    def abort(self, task_id: str) -> bool:
        if task_id not in self.results:
            return False
        return self.engine.cancel(task_id)
