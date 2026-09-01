"""Turning a username into the uid and gid a daemon must act as."""

import pwd
from typing import Protocol

from stashd.domain.errors import NotFound
from stashd.domain.identity import Principal
from stashd.domain.storage import Owner


class OwnerLookup(Protocol):
    def by_name(self, user: str) -> Owner: ...


class SystemOwnerLookup:
    def by_name(self, user: str) -> Owner:
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            raise NotFound(f"no user named {user}", user=user) from None
        return Owner(user=user, uid=entry.pw_uid, gid=entry.pw_gid)


def owner_for(principal: Principal, subject: str, lookup: OwnerLookup) -> Owner:
    """The caller's own identity is already verified; anyone else must be looked up."""
    if subject == principal.username:
        return Owner(user=principal.username, uid=principal.uid, gid=principal.gid)
    return lookup.by_name(subject)
