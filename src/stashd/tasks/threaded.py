"""Running transfers beside the request that asked for them.

A request handler must never move data, and the daemon must not run more transfers at
once than it was sized for.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import count
from threading import Lock

import structlog

from stashd.clients.events import EventReporter
from stashd.drivers.base import StorageDriver
from stashd.engines.base import TransferEngine
from stashd.tasks.execute import execute_transfer, progress_reporter
from stashd.tasks.runner import TaskProgress, TransferTask

logger = structlog.get_logger()


@dataclass
class ThreadTaskRunner:
    drivers: dict[str, StorageDriver]
    engine: TransferEngine
    reporter: EventReporter
    pool_size: int = 4
    _states: dict[str, TaskProgress] = field(default_factory=dict)
    _futures: dict[str, "Future[None]"] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _ids: "count[int]" = field(default_factory=lambda: count(1))
    _pool: ThreadPoolExecutor | None = None

    def __post_init__(self) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=self.pool_size, thread_name_prefix="transfer"
        )

    def submit(self, task: TransferTask) -> str:
        task_id = f"t{next(self._ids)}"
        with self._lock:
            self._states[task_id] = TaskProgress(
                task_id=task_id, transfer_id=task.transfer_id, state="SUBMITTED"
            )
        assert self._pool is not None
        self._futures[task_id] = self._pool.submit(self._run, task, task_id)
        return task_id

    def progress(self, task_id: str) -> TaskProgress | None:
        with self._lock:
            return self._states.get(task_id)

    def abort(self, task_id: str) -> bool:
        with self._lock:
            known = task_id in self._states
        return self.engine.cancel(task_id) if known else False

    def _run(self, task: TransferTask, task_id: str) -> None:
        self.reporter.report(task.transfer_id, "started")
        self._set(task_id, TaskProgress(task_id, task.transfer_id, "RUNNING"))
        try:
            done = execute_transfer(
                self.drivers, self.engine, task, task_id, progress_reporter(self.reporter, task)
            )
        except Exception as error:  # a bug here must still reach the controller
            logger.exception("transfer.crashed", task_id=task_id, transfer_id=task.transfer_id)
            self._set(
                task_id, TaskProgress(task_id, task.transfer_id, "FAILED", message=str(error))
            )
            self.reporter.report(
                task.transfer_id, "failed", failure="internal", message=str(error)
            )
            return
        self._set(task_id, done)
        self.reporter.report(
            task.transfer_id,
            "finished" if done.state == "SUCCEEDED" else "failed",
            bytes_done=done.bytes_done,
            files_done=done.files_done,
            failure=str(done.failure) if done.failure else None,
            message=done.message,
        )

    def _set(self, task_id: str, state: TaskProgress) -> None:
        with self._lock:
            self._states[task_id] = state
