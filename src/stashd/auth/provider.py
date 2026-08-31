"""The authentication seam: a credential in, a verified principal out."""

from typing import Protocol

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.identity import Principal


class Unauthenticated(StashError):
    code = ErrorCode.UNAUTHENTICATED


class AuthProvider(Protocol):
    """Turns the credential of one request into a verified principal."""

    scheme: str

    def authenticate(self, credential: str) -> Principal: ...


class IdentityLookup(Protocol):
    """Resolves a verified uid/gid into a username and its groups."""

    def lookup(self, uid: int, gid: int) -> tuple[str, tuple[str, ...], tuple[int, ...]]: ...
