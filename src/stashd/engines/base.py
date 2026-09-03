"""The transfer seam.

Choosing the channel from the pair of daemons is domain logic; running the tool is not.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner


@dataclass(frozen=True, slots=True)
class TransferEndpoint:
    """One end of a transfer: a local path, or a path on another host over SSH."""

    path: str
    host: str | None = None
    user: str | None = None

    @property
    def is_remote(self) -> bool:
        return self.host is not None

    def as_argument(self) -> str:
        """rsync copies the *contents* of a directory, so both ends carry a slash."""
        path = self.path.rstrip("/") + "/"
        if self.host is None:
            return path
        account = f"{self.user}@" if self.user else ""
        return f"{account}{self.host}:{path}"


@dataclass(frozen=True, slots=True)
class TransferOptions:
    bwlimit_bytes_per_s: int | None = None
    # Only a cached-fileset refresh deletes at the target; a flush never does.
    delete: bool = False
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TransferResult:
    failure: FailureClass | None
    returncode: int
    bytes_transferred: int
    files_transferred: int
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.failure is None


@dataclass(frozen=True, slots=True)
class Progress:
    bytes_done: int


ProgressCallback = Callable[[Progress], None]


class TransferEngine(Protocol):
    def run(
        self,
        source: TransferEndpoint,
        target: TransferEndpoint,
        options: TransferOptions,
        *,
        owner: Owner,
        handle: str,
        on_progress: ProgressCallback | None = None,
    ) -> TransferResult: ...

    def cancel(self, handle: str) -> bool: ...
