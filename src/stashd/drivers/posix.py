"""The POSIX driver: a fileset is a directory owned by its user.

Everything that touches user data runs through the ``Identity``, so the daemon never
holds a privileged handle to a fileset. There is no quota enforcement here: on plain
POSIX an overrun is only detectable afterwards.
"""

from pathlib import Path

from stashd.domain.errors import InvalidPath
from stashd.domain.references import validate_fileset_name
from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.base import DriverError, StorageCapabilities
from stashd.identity.base import Identity

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
    ) -> None:
        self.storage_id = storage_id
        self._prefix = fileset_prefix
        self._identity = identity
        self._mode = fileset_mode

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
