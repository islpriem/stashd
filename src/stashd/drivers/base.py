"""The storage abstraction.

Drivers are the only code that touches user data. Callers branch on
``driver.capabilities``, never on the driver's type or id.
"""

from dataclasses import dataclass
from typing import Protocol

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.storage import FilesetLocation, Owner


class DriverError(StashError):
    code = ErrorCode.INTERNAL


class PermissionDenied(StashError):
    code = ErrorCode.PERMISSION_DENIED


@dataclass(frozen=True, slots=True)
class StorageCapabilities:
    native_quota: bool
    fast_usage: bool
    atomic_delete: bool
    remote_endpoint: bool


class StorageDriver(Protocol):
    storage_id: str
    capabilities: StorageCapabilities

    def fileset_path(self, owner: Owner, name: str) -> str:
        """Where a fileset of this owner lives; deterministic."""
        ...

    def create_fileset(self, owner: Owner, name: str, allocation: int) -> FilesetLocation:
        """Create the directory, owned by ``owner``, with the storage's mode."""
        ...

    def set_fileset_quota(self, location: FilesetLocation, allocation: int) -> None:
        """Enforce the allocation on the storage, where the driver can."""
        ...

    def delete_fileset(self, location: FilesetLocation) -> None:
        """Remove the directory and everything in it. Already gone is success.

        Runs as ``location.owner``: a fileset is never touched as anyone else.
        """
        ...
