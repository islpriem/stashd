"""The identity seam: run one command as one user."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.storage import Owner

DEFAULT_TIMEOUT = 60.0


class IdentityError(StashError):
    code = ErrorCode.PERMISSION_DENIED


@dataclass(frozen=True, slots=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Identity(Protocol):
    """Runs a command as ``owner``. Implementations differ only in how privilege is got."""

    def run(
        self, owner: Owner, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT
    ) -> Completed: ...
