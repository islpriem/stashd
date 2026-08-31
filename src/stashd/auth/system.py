"""Username and groups from the host's name service (A2: consistent across sites)."""

import grp
import pwd

from stashd.auth.provider import Unauthenticated


class SystemIdentityLookup:
    def lookup(self, uid: int, gid: int) -> tuple[str, tuple[str, ...], tuple[int, ...]]:
        """The primary group comes from the credential; the rest from the group file."""
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            raise Unauthenticated(f"uid {uid} is unknown on the controller", uid=uid) from None
        memberships = {
            group.gr_name: group.gr_gid for group in grp.getgrall() if username in group.gr_mem
        }
        try:
            primary = grp.getgrgid(gid)
            memberships[primary.gr_name] = primary.gr_gid
        except KeyError:
            pass
        return username, tuple(memberships), tuple(memberships.values())
