"""Value objects passed to and from a storage driver. Pure: no filesystem here."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Owner:
    """Who an operation runs as. Comes from the credential, never from a request field."""

    user: str
    uid: int
    gid: int


@dataclass(frozen=True, slots=True)
class FilesetLocation:
    storage_id: str
    name: str
    owner: Owner
    path: str


@dataclass(frozen=True, slots=True)
class UsageReport:
    used_bytes: int
    file_count: int | None = None
