"""An engine that moves nothing, for tests and for a dry cluster."""

from dataclasses import dataclass, field

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.engines.base import (
    Progress,
    ProgressCallback,
    TransferEndpoint,
    TransferOptions,
    TransferResult,
)


@dataclass
class Call:
    source: TransferEndpoint
    target: TransferEndpoint
    options: TransferOptions
    owner: Owner
    handle: str


@dataclass
class FakeEngine:
    bytes_transferred: int = 1024
    files_transferred: int = 1
    failure: FailureClass | None = None
    progress_steps: tuple[int, ...] = ()
    calls: list[Call] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def run(
        self,
        source: TransferEndpoint,
        target: TransferEndpoint,
        options: TransferOptions,
        *,
        owner: Owner,
        handle: str,
        on_progress: ProgressCallback | None = None,
    ) -> TransferResult:
        self.calls.append(Call(source, target, options, owner, handle))
        for step in self.progress_steps:
            if on_progress is not None:
                on_progress(Progress(bytes_done=step))
        return TransferResult(
            failure=self.failure,
            returncode=0 if self.failure is None else 23,
            bytes_transferred=self.bytes_transferred,
            files_transferred=self.files_transferred,
        )

    def cancel(self, handle: str) -> bool:
        self.cancelled.append(handle)
        return True
