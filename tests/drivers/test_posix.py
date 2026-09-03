"""The posix driver against a real filesystem under tmp_path."""

import os
import stat
from pathlib import Path

import pytest

from stashd.domain.errors import StashError
from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.posix import PosixDriver
from stashd.identity.current import CurrentUserIdentity
from tests.drivers.conformance import DriverConformance, Probe


class TestPosixDriver(DriverConformance):
    @pytest.fixture
    def prefix(self, tmp_path: Path) -> Path:
        root = tmp_path / "cache"
        root.mkdir()
        return root

    @pytest.fixture
    def driver(self, prefix: Path) -> PosixDriver:
        return PosixDriver(
            storage_id="LOC2HOT", fileset_prefix=prefix, identity=CurrentUserIdentity()
        )

    @pytest.fixture
    def owner(self) -> Owner:
        """The identity is the test user itself: nothing here needs privilege."""
        return Owner(user="tester", uid=os.getuid(), gid=os.getgid())

    def probe(self, location: FilesetLocation) -> Probe:
        path = Path(location.path)
        if not path.exists():
            return Probe(exists=False)
        info = path.stat()
        return Probe(
            exists=True,
            uid=info.st_uid,
            mode=stat.S_IMODE(info.st_mode),
            entries=tuple(sorted(entry.name for entry in path.iterdir())),
        )

    def seed_file(self, location: FilesetLocation, name: str) -> None:
        (Path(location.path) / name).write_text("payload")


class TestPosixEdgeCases:
    @pytest.fixture
    def driver(self, tmp_path: Path) -> PosixDriver:
        return PosixDriver(
            storage_id="LOC2HOT", fileset_prefix=tmp_path, identity=CurrentUserIdentity()
        )

    @pytest.fixture
    def owner(self) -> Owner:
        return Owner(user="tester", uid=os.getuid(), gid=os.getgid())

    def test_posix_cannot_enforce_a_quota_and_says_so(self, driver: PosixDriver) -> None:
        assert driver.capabilities.native_quota is False

    def test_the_mode_is_configurable_per_storage(self, tmp_path: Path, owner: Owner) -> None:
        driver = PosixDriver(
            storage_id="LOC2HOT",
            fileset_prefix=tmp_path,
            identity=CurrentUserIdentity(),
            fileset_mode=0o750,
        )

        location = driver.create_fileset(owner, "mydir", 1024)

        assert stat.S_IMODE(Path(location.path).stat().st_mode) == 0o750

    def test_the_per_user_directory_is_created_on_the_way(
        self, driver: PosixDriver, owner: Owner, tmp_path: Path
    ) -> None:
        driver.create_fileset(owner, "mydir", 1024)

        assert (tmp_path / owner.user).is_dir()

    def test_a_directory_owned_by_someone_else_is_never_adopted(
        self, driver: PosixDriver, owner: Owner, tmp_path: Path
    ) -> None:
        squatted = tmp_path / owner.user / "mydir"
        squatted.mkdir(parents=True)
        stranger = Owner(user=owner.user, uid=owner.uid + 1, gid=owner.gid)

        with pytest.raises(StashError, match="belongs to"):
            driver.create_fileset(stranger, "mydir", 1024)

    def test_a_failing_command_is_reported_with_its_message(
        self, tmp_path: Path, owner: Owner
    ) -> None:
        driver = PosixDriver(
            storage_id="LOC2HOT",
            fileset_prefix=tmp_path / "does" / "not" / "exist",
            identity=CurrentUserIdentity(),
        )
        (tmp_path / "does").write_text("a file, not a directory")

        with pytest.raises(StashError):
            driver.create_fileset(owner, "mydir", 1024)

    def test_deleting_leaves_the_other_filesets_alone(
        self, driver: PosixDriver, owner: Owner
    ) -> None:
        keep = driver.create_fileset(owner, "keep", 1024)
        drop = driver.create_fileset(owner, "drop", 1024)

        driver.delete_fileset(drop)

        assert Path(keep.path).is_dir()
        assert not Path(drop.path).exists()

    def test_a_symlink_out_of_the_storage_is_refused(
        self, driver: PosixDriver, tmp_path: Path, owner: Owner
    ) -> None:
        """The path is resolved before it is checked, so a link cannot be followed out."""
        (tmp_path / "escape").symlink_to("/etc")

        with pytest.raises(StashError, match="outside"):
            driver.resolve("/escape")

    def test_a_probe_reports_what_is_there(
        self, driver: PosixDriver, tmp_path: Path, owner: Owner
    ) -> None:
        (tmp_path / "myuser").mkdir()
        (tmp_path / "myuser" / "one").write_bytes(b"x" * 4096)
        (tmp_path / "myuser" / "two").write_bytes(b"y" * 4096)
        resolved = driver.resolve("/myuser")

        found = driver.stat(resolved, owner=owner)
        measured = driver.measure(resolved, owner=owner, timeout=10)

        assert (found.exists, found.is_dir, found.readable) == (True, True, True)
        assert measured.file_count == 2
        assert measured.bytes_total >= 8192
        assert measured.complete

    def test_a_path_that_is_not_there_is_reported_as_absent(
        self, driver: PosixDriver, owner: Owner
    ) -> None:
        found = driver.stat(driver.resolve("/nothing/here"), owner=owner)

        assert not found.exists
        assert not found.readable

    def test_measuring_something_that_is_not_there_is_an_error(
        self, driver: PosixDriver, owner: Owner
    ) -> None:
        with pytest.raises(StashError, match="cannot measure"):
            driver.measure(driver.resolve("/nothing/here"), owner=owner, timeout=10)

    def test_the_storage_root_itself_resolves(
        self, driver: PosixDriver, tmp_path: Path
    ) -> None:
        assert driver.resolve("/").absolute == str(tmp_path.resolve())

    def test_the_prefix_itself_can_never_be_deleted(
        self, driver: PosixDriver, owner: Owner, tmp_path: Path
    ) -> None:
        location = FilesetLocation(
            storage_id="LOC2HOT", name="mydir", owner=owner, path=str(tmp_path)
        )

        with pytest.raises(StashError):
            driver.delete_fileset(location)

        assert tmp_path.is_dir()
