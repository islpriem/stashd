"""A provider for tests and for the development stack: a credential is a name."""

from collections.abc import Mapping

from stashd.auth.provider import Unauthenticated
from stashd.domain.identity import Principal


class FakeAuthProvider:
    scheme = "Munge"

    def __init__(self, principals: Mapping[str, Principal]) -> None:
        self._principals = dict(principals)

    def authenticate(self, credential: str) -> Principal:
        principal = self._principals.get(credential)
        if principal is None:
            raise Unauthenticated("unknown credential")
        return principal
