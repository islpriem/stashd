"""The seam between a request and the work it starts.

A request handler never moves data: it hands a task to a runner and answers. Celery is
the runner in a deployment; tests use one that runs the transfer inline.
"""

from dataclasses import dataclass
from typing import Protocol

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.engines.base import TransferEndpoint


@dataclass(frozen=True, slots=True)
class TransferTask:
    transfer_id: int
    storage_id: str
    source_path: str
    owner: Owner
    target: TransferEndpoint
    bwlimit_bytes_per_s: int | None = None
    delete: bool = False


@dataclass(frozen=True, slots=True)
class TaskProgress:
    task_id: str
    transfer_id: int
    state: str
    bytes_done: int = 0
    files_done: int = 0
    failure: FailureClass | None = None
    message: str = ""


class TaskRunner(Protocol):
    def submit(self, task: TransferTask) -> str: ...

    def progress(self, task_id: str) -> TaskProgress | None: ...

    def abort(self, task_id: str) -> bool: ...
