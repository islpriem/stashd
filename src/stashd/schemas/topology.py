"""Identity and topology responses."""

from stashd.config.cluster import Driver, StorageRole, Tier
from stashd.schemas.base import Wire


class WhoAmI(Wire):
    uid: int
    username: str
    groups: list[str]
    admin: bool
    server_version: str
    api_version: str


class Location(Wire):
    id: str
    name: str
    enabled: bool


class Locations(Wire):
    locations: list[Location]


class Storage(Wire):
    id: str
    location: str
    roles: list[StorageRole]
    tier: Tier
    driver: Driver
    fileset_prefix: str
    capacity_bytes: int | None
    fill_limit: float
    default_user_allocation_limit_bytes: int | None
    daemon: str
    drained: bool
    enabled: bool
    # False where the driver cannot hold a directory quota: the allocation is then a
    # reservation in STASH, and an overrun is only found afterwards.
    quota_enforced: bool


class Storages(Wire):
    storages: list[Storage]
