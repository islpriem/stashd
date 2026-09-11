"""An in-memory driver for tests and for a controller without storage.

It declares the capabilities the posix driver lacks, so the shared conformance suite
covers both sides of every capability branch.
"""

from dataclasses import dataclass, field

from stashd.domain.errors import InvalidPath
from stashd.domain.references import validate_fileset_name, validate_storage_path
from stashd.domain.storage import (
    FilesetLocation,
    Owner,
    PathStat,
    ResolvedPath,
    SizeReport,
    UsageReport,
)
from stashd.drivers.base import DriverError, StorageCapabilities
from stashd.engines.base import TransferEndpoint

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
        root: str | None = None,
    ) -> None:
        self.storage_id = storage_id
        self.filesets: dict[str, FakeFileset] = {}
        self.failures: dict[str, Exception] = {}
        self.paths: dict[str, PathStat] = {}
        self.sizes: dict[str, SizeReport] = {}
        self._prefix = fileset_prefix.rstrip("/")
        self._root = (root or fileset_prefix).rstrip("/")
        self._mode = fileset_mode

    def resolve(self, storage_relative_path: str) -> ResolvedPath:
        relative = validate_storage_path(self.storage_id, storage_relative_path)
        return ResolvedPath(
            storage_id=self.storage_id,
            relative=relative,
            absolute=f"{self._root}{relative}".rstrip("/") or self._root,
        )

    def stat(self, path: ResolvedPath, *, owner: Owner) -> PathStat:
        del owner
        known = self.paths.get(path.absolute)
        if known is not None:
            return known
        if path.absolute in self.filesets:
            # A fileset the driver made is a directory its owner may write into.
            return PathStat(exists=True, is_dir=True, readable=True, writable=True)
        return PathStat(exists=False, is_dir=False, readable=False, writable=False)

    def measure(self, path: ResolvedPath, *, owner: Owner, timeout: float) -> SizeReport:
        del owner, timeout
        self._maybe_fail("measure")
        return self.sizes.get(path.absolute, SizeReport(bytes_total=0, file_count=0))

    def endpoint(self, path: str) -> TransferEndpoint:
        return TransferEndpoint(path=path)

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

    def fileset_usage(self, location: FilesetLocation) -> UsageReport:
        self._check_inside_prefix(location)
        fileset = self.filesets.get(location.path)
        if fileset is None:
            return UsageReport(used_bytes=0, file_count=0)
        return UsageReport(
            used_bytes=sum(fileset.files.values()), file_count=len(fileset.files)
        )

    def storage_usage(self) -> UsageReport:
        return UsageReport(
            used_bytes=sum(sum(fileset.files.values()) for fileset in self.filesets.values()),
            file_count=sum(len(fileset.files) for fileset in self.filesets.values()),
        )

    def delete_fileset(self, location: FilesetLocation) -> None:
        self._maybe_fail("delete_fileset")
        self._check_inside_prefix(location)
        self.filesets.pop(location.path, None)

    def _check_inside_prefix(self, location: FilesetLocation) -> None:
        """The path in the request is never trusted; it is re-derived here."""
        expected = f"{self._prefix}/{location.owner.user}/{location.name}"
        if location.path != expected:
            raise InvalidPath(f"{location.path} is not a fileset path", path=location.path)

    def with_source(self, path: str, *, bytes_total: int, file_count: int) -> str:
        """Pretend a readable directory of that size is there, for a probe to find."""
        absolute = f"{self._root}{path}"
        self.paths[absolute] = PathStat(exists=True, is_dir=True, readable=True)
        self.sizes[absolute] = SizeReport(bytes_total=bytes_total, file_count=file_count)
        return absolute

    def fail_on(self, operation: str, error: Exception) -> None:
        """Make the next call to `operation` raise, so callers can be tested."""
        self.failures[operation] = error

    def _maybe_fail(self, operation: str) -> None:
        error = self.failures.pop(operation, None)
        if error is not None:
            raise error
