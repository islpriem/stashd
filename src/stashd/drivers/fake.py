"""An in-memory driver for tests and for a controller without storage.

It declares the capabilities the posix driver lacks, so the shared conformance suite
covers both sides of every capability branch.
"""

from dataclasses import dataclass, field

from stashd.domain.errors import InvalidPath
from stashd.domain.references import validate_fileset_name
from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.base import DriverError, StorageCapabilities

DEFAULT_MODE = 0o700


@dataclass
class FakeFileset:
    owner: Owner
    mode: int
    quota_bytes: int
    files: dict[str, int] = field(default_factory=dict)


class FakeDriver:
    capabilities = StorageCapabilities(
        native_quota=True, fast_usage=True, atomic_delete=True, remote_endpoint=False
    )

    def __init__(
        self,
        storage_id: str = "LOC2HOT",
        fileset_prefix: str = "/fake/cache",
        fileset_mode: int = DEFAULT_MODE,
    ) -> None:
        self.storage_id = storage_id
        self.filesets: dict[str, FakeFileset] = {}
        self.failures: dict[str, Exception] = {}
        self._prefix = fileset_prefix.rstrip("/")
        self._mode = fileset_mode

    def fileset_path(self, owner: Owner, name: str) -> str:
        return f"{self._prefix}/{owner.user}/{validate_fileset_name(name)}"

    def create_fileset(self, owner: Owner, name: str, allocation: int) -> FilesetLocation:
        self._maybe_fail("create_fileset")
        path = self.fileset_path(owner, name)
        existing = self.filesets.get(path)
        if existing is not None and existing.owner.uid != owner.uid:
            raise DriverError(
                f"{path} belongs to uid {existing.owner.uid}", storage=self.storage_id
            )
        self.filesets.setdefault(
            path, FakeFileset(owner=owner, mode=self._mode, quota_bytes=allocation)
        )
        return FilesetLocation(storage_id=self.storage_id, name=name, owner=owner, path=path)

    def set_fileset_quota(self, location: FilesetLocation, allocation: int) -> None:
        self._maybe_fail("set_fileset_quota")
        fileset = self.filesets.get(location.path)
        if fileset is not None:
            fileset.quota_bytes = allocation

    def delete_fileset(self, location: FilesetLocation) -> None:
        self._maybe_fail("delete_fileset")
        expected = f"{self._prefix}/{location.owner.user}/{location.name}"
        if location.path != expected:
            raise InvalidPath(f"{location.path} is not a fileset path", path=location.path)
        self.filesets.pop(location.path, None)

    def fail_on(self, operation: str, error: Exception) -> None:
        """Make the next call to `operation` raise, so callers can be tested."""
        self.failures[operation] = error

    def _maybe_fail(self, operation: str) -> None:
        error = self.failures.pop(operation, None)
        if error is not None:
            raise error
