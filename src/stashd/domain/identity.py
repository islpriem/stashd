"""Who is asking, and whether they are an admin.

Authorization derives only from the credential-verified UID and GID, never from a
username a client sent.
"""

from collections.abc import Collection
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    uid: int
    gid: int
    username: str
    groups: tuple[str, ...] = ()
    gids: tuple[int, ...] = ()


def is_admin(
    principal: Principal, *, admin_uids: Collection[int], admin_gids: Collection[int | str]
) -> bool:
    """UID 0, a listed UID, or membership in a listed group by name or by gid."""
    if principal.uid == 0 or principal.uid in admin_uids:
        return True
    names = {group for group in admin_gids if isinstance(group, str)}
    numbers = {group for group in admin_gids if isinstance(group, int)}
    return bool(names & set(principal.groups)) or bool(
        numbers & ({principal.gid} | set(principal.gids))
    )
