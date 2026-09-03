"""Become the user with ``sudo -n -u``.

Non-interactive by construction: if sudo would ask for anything, the operation fails
rather than hanging. The sudoers entry a deployment needs is in docs/OPERATIONS.md.
"""

import subprocess
from collections.abc import Callable, Sequence

from stashd.domain.storage import Owner
from stashd.identity.base import DEFAULT_TIMEOUT, Completed, IdentityError

Runner = Callable[[Sequence[str], float], Completed]


def run_subprocess(argv: Sequence[str], timeout: float) -> Completed:  # pragma: no cover - I/O
    finished = subprocess.run(
        list(argv), capture_output=True, text=True, timeout=timeout, check=False
    )
    return Completed(finished.returncode, finished.stdout, finished.stderr)


class SudoIdentity:
    def __init__(self, runner: Runner = run_subprocess) -> None:
        self._run = runner

    def wrap(self, owner: Owner, argv: Sequence[str]) -> list[str]:
        # Privilege comes from a sudoers rule for this daemon, not from STASH.
        return ["sudo", "-n", "-u", owner.user, "--", *argv]

    def run(
        self, owner: Owner, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT
    ) -> Completed:
        command = self.wrap(owner, argv)
        try:
            return self._run(command, timeout)
        except subprocess.TimeoutExpired as expired:
            raise IdentityError(
                f"running as {owner.user} timed out after {timeout:g}s", user=owner.user
            ) from expired
        except OSError as error:
            raise IdentityError(
                f"cannot run as {owner.user}: {error}", user=owner.user
            ) from error
