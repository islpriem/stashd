"""No privilege change at all: the daemon's own user does the work.

What development uses, where the storage roots are inside the checkout and everything
already belongs to the developer. In production this is only correct for a daemon that
serves a single user's data.
"""

import subprocess
from collections.abc import Sequence

from stashd.domain.storage import Owner
from stashd.identity.base import DEFAULT_TIMEOUT, Completed, IdentityError


class CurrentUserIdentity:
    def run(
        self, owner: Owner, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT
    ) -> Completed:
        del owner  # the daemon's own user does the work; that is what this is for
        try:
            finished = subprocess.run(
                list(argv), capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as expired:
            raise IdentityError(f"{argv[0]} timed out after {timeout:g}s") from expired
        except OSError as error:
            raise IdentityError(f"cannot run {argv[0]}: {error}") from error
        return Completed(finished.returncode, finished.stdout, finished.stderr)
