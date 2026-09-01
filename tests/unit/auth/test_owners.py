"""Resolving a username into the uid a daemon must act as."""

import getpass
import os

import pytest

from stashd.auth.owners import SystemOwnerLookup, owner_for
from stashd.domain.errors import NotFound
from stashd.domain.identity import Principal


def test_a_known_user_resolves_to_their_uid_and_gid() -> None:
    owner = SystemOwnerLookup().by_name(getpass.getuser())

    assert owner.uid == os.getuid()
    assert owner.gid == os.getgid()


def test_an_unknown_user_is_not_found() -> None:
    with pytest.raises(NotFound):
        SystemOwnerLookup().by_name("definitely-not-a-user-42")


def test_the_callers_own_identity_is_never_looked_up() -> None:
    class Explode:
        def by_name(self, user: str) -> object:
            raise AssertionError("must not be called")

    caller = Principal(uid=1000, gid=1000, username="mmustermann")

    owner = owner_for(caller, "mmustermann", Explode())  # type: ignore[arg-type]

    assert (owner.user, owner.uid, owner.gid) == ("mmustermann", 1000, 1000)
