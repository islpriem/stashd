"""The POSIX driver: a fileset is a directory owned by its user.

Everything that touches user data runs through the ``Identity``, so the daemon never
holds a privileged handle to a fileset. There is no quota enforcement here: on plain
POSIX an overrun is only detectable afterwards.
"""

from pathlib import Path

from stashd.domain.errors import InvalidPath
from stashd.domain.references import validate_fileset_name, validate_storage_path
from stashd.domain.storage import (
    FilesetLocation,
    Owner,
    PathStat,
    ResolvedPath,
    SizeReport,
)
from stashd.drivers.base import DriverError, StorageCapabilities
from stashd.engines.base import TransferEndpoint
from stashd.identity.base import Identity, IdentityError

DEFAULT_MODE = 0o700


class PosixDriver:
    capabilities = StorageCapabilities(
        native_quota=False, fast_usage=False, atomic_delete=False, remote_endpoint=True
    )

    def __init__(
        self,
        storage_id: str,
        fileset_prefix: Path,
        identity: Identity,
        fileset_mode: int = DEFAULT_MODE,
        root: Path | None = None,
    ) -> None:
        self.storage_id = storage_id
        self._prefix = fileset_prefix
        self._root = root or fileset_prefix
        self._identity = identity
        self._mode = fileset_mode

    def resolve(self, storage_relative_path: str) -> ResolvedPath:
        """Re-resolved here, on the daemon that owns the filesystem.

        Symlinks are followed before the check, so a link out of the storage is refused
        rather than followed.
        """
        relative = validate_storage_path(self.storage_id, storage_relative_path)
        candidate = (self._root / relative.lstrip("/")).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise InvalidPath(
                f"{self.storage_id}:{relative} resolves outside {self.storage_id}",
                path=relative,
                storage=self.storage_id,
            )
        return ResolvedPath(
            storage_id=self.storage_id, relative=relative, absolute=str(candidate)
        )

    def stat(self, path: ResolvedPath, *, owner: Owner) -> PathStat:
        """Asked as the owner: STASH never reports what the user cannot see themselves."""
        exists = self._identity.run(owner, ["test", "-e", path.absolute]).ok
        if not exists:
            return PathStat(exists=False, is_dir=False, readable=False, writable=False)
        return PathStat(
            exists=True,
            is_dir=self._identity.run(owner, ["test", "-d", path.absolute]).ok,
            readable=self._identity.run(owner, ["test", "-r", path.absolute]).ok,
            writable=self._identity.run(owner, ["test", "-w", path.absolute]).ok,
        )

    def measure(self, path: ResolvedPath, *, owner: Owner, timeout: float) -> SizeReport:
        """Disk usage and file count, as the owner.

        ``du -sk`` is what every POSIX host has; the estimate is therefore disk usage at
        kibibyte granularity, which is what the target will consume.
        """
        try:
            usage = self._identity.run(owner, ["du", "-sk", path.absolute], timeout=timeout)
            listing = self._identity.run(
                owner, ["find", path.absolute, "-type", "f"], timeout=timeout
            )
        except IdentityError:
            return SizeReport(bytes_total=0, file_count=0, complete=False)
        if not usage.ok:
            raise DriverError(
                f"cannot measure {path.relative} on {self.storage_id}: "
                f"{usage.stderr.strip() or 'du failed'}",
                storage=self.storage_id,
            )
        kibibytes = int(usage.stdout.split(maxsplit=1)[0] or 0)
        return SizeReport(
            bytes_total=kibibytes * 1024,
            file_count=len([line for line in listing.stdout.splitlines() if line]),
            complete=listing.ok,
        )

    def endpoint(self, path: str) -> TransferEndpoint:
        return TransferEndpoint(path=path)

    def fileset_path(self, owner: Owner, name: str) -> str:
        return str(self._prefix / owner.user / validate_fileset_name(name))

    def create_fileset(self, owner: Owner, name: str, allocation: int) -> FilesetLocation:
        del allocation  # a driver with native_quota would apply it here
        path = self.fileset_path(owner, name)
        self._run(owner, ["mkdir", "-p", path])
        self._run(owner, ["chmod", f"{self._mode:04o}", path])
        self._check_ownership(path, owner)
        return FilesetLocation(storage_id=self.storage_id, name=name, owner=owner, path=path)

    def set_fileset_quota(self, location: FilesetLocation, allocation: int) -> None:
        """A documented no-op: plain POSIX has no per-directory quota.

        Overruns are caught afterwards by usage reconciliation, which flags the fileset
        `over_allocation` and blocks further allocations by that user.
        """
        del location, allocation

    def delete_fileset(self, location: FilesetLocation) -> None:
        self._check_inside_prefix(location)
        self._run(location.owner, ["rm", "-rf", "--", location.path])

    def _run(self, owner: Owner, argv: list[str]) -> None:
        result = self._identity.run(owner, argv)
        if not result.ok:
            raise DriverError(
                f"{argv[0]} failed on {self.storage_id}: "
                f"{result.stderr.strip() or 'no output'}",
                storage=self.storage_id,
            )

    def _check_ownership(self, path: str, owner: Owner) -> None:
        """Never adopt a directory that is already someone else's."""
        found = Path(path).stat().st_uid
        if found != owner.uid:
            raise DriverError(
                f"{path} belongs to uid {found}, not to {owner.user} (uid {owner.uid})",
                storage=self.storage_id,
            )

    def _check_inside_prefix(self, location: FilesetLocation) -> None:
        """The controller's path is never trusted: it is re-checked here."""
        candidate = Path(location.path)
        expected = self._prefix / location.owner.user / location.name
        if candidate != expected or self._prefix not in candidate.parents:
            raise InvalidPath(
                f"{location.path} is not a fileset of {location.owner.user} "
                f"on {self.storage_id}",
                path=location.path,
            )
