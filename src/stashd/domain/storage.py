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


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A storage-relative path turned into a real one, proven to be inside the storage."""

    storage_id: str
    relative: str
    absolute: str


@dataclass(frozen=True, slots=True)
class PathStat:
    exists: bool
    is_dir: bool
    readable: bool


@dataclass(frozen=True, slots=True)
class SizeReport:
    """What a probe found. ``complete`` is false when the measurement ran out of time."""

    bytes_total: int
    file_count: int
    complete: bool = True
