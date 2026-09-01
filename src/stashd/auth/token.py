"""Peer authentication for the internal API.

A shared bearer token read from a 0600 file at startup. MUNGE is deliberately not used
here: sites may be in different MUNGE realms, and tokens rotate per peer.
"""

import secrets
import stat
from pathlib import Path

from stashd.auth.provider import Unauthenticated
from stashd.config.errors import ConfigError


class TokenAuthProvider:
    scheme = "Bearer"

    def __init__(self, token: str) -> None:
        self._token = token

    @classmethod
    def from_file(cls, path: Path) -> "TokenAuthProvider":
        if not path.is_file():
            raise ConfigError(path, [f"{path} does not exist"])
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ConfigError(path, [f"{path} is mode {mode:04o}: a peer token must be 0600"])
        token = path.read_text().strip()
        if not token:
            raise ConfigError(path, [f"{path} is empty"])
        return cls(token)

    def verify(self, credential: str) -> None:
        if not credential or not secrets.compare_digest(credential, self._token):
            raise Unauthenticated("the peer token was not accepted")
