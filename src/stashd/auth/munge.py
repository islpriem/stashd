"""MUNGE: the CLI proves a UID/GID per request.

Credentials are single-use and carry their own TTL and replay protection; the controller
only decodes them. It never learns a password and never impersonates anyone.
"""

from pathlib import Path
from typing import Protocol

from stashd.auth.provider import IdentityLookup, Unauthenticated
from stashd.domain.identity import Principal


class MungeDecoder(Protocol):
    def __call__(self, credential: str, socket: Path) -> tuple[int, int]: ...


def decode_with_libmunge(credential: str, socket: Path) -> tuple[int, int]:  # pragma: no cover
    """Real decoding; imported lazily so a controller-less host can import this module."""
    import pymunge

    with pymunge.MungeContext() as context:
        context.socket = str(socket)
        _, uid, gid = context.decode(credential.encode())
        return int(uid), int(gid)


class MungeAuthProvider:
    scheme = "Munge"

    def __init__(
        self,
        socket_path: Path,
        lookup: IdentityLookup,
        decoder: MungeDecoder = decode_with_libmunge,
    ) -> None:
        self._socket_path = socket_path
        self._lookup = lookup
        self._decode = decoder

    def authenticate(self, credential: str) -> Principal:
        try:
            uid, gid = self._decode(credential, self._socket_path)
        except Exception:
            # The reason belongs in the log, never in the response: it describes a credential.
            raise Unauthenticated("the MUNGE credential was not accepted") from None
        username, groups, gids = self._lookup.lookup(uid, gid)
        return Principal(uid=uid, gid=gid, username=username, groups=groups, gids=gids)
