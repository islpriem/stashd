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
    """Becoming ``owner``. Implementations differ only in how privilege is got.

    ``run`` is for the short commands a driver issues. ``wrap`` hands back the same
    command for a caller that must own the process itself — the transfer engine streams
    progress and has to be able to kill the whole process group.
    """

    def run(
        self, owner: Owner, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT
    ) -> Completed: ...

    def wrap(self, owner: Owner, argv: Sequence[str]) -> list[str]: ...
