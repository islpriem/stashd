"""The suite every StorageDriver must pass unchanged.

A subclass provides the driver and two hooks that look at the storage behind its back;
every assertion about behaviour goes through the driver itself.
"""

from dataclasses import dataclass

import pytest

from stashd.domain.errors import StashError
from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.base import StorageDriver


@dataclass(frozen=True)
class Probe:
    exists: bool
    uid: int | None = None
    mode: int | None = None
    entries: tuple[str, ...] = ()


class DriverConformance:
    """Mix into a test class that defines `driver`, `owner`, `probe` and `seed_file`."""

    @pytest.fixture
    def driver(self) -> StorageDriver:
        raise NotImplementedError

    @pytest.fixture
    def owner(self) -> Owner:
        raise NotImplementedError

    def probe(self, location: FilesetLocation) -> Probe:
        raise NotImplementedError

    def seed_file(self, location: FilesetLocation, name: str) -> None:
        raise NotImplementedError

    def relative_of(self, location: FilesetLocation) -> str:
        """The location as the storage names it: filesets live at /<user>/<name>."""
        return f"/{location.owner.user}/{location.name}"

    def test_the_path_is_prefix_user_name(self, driver: StorageDriver, owner: Owner) -> None:
        path = driver.fileset_path(owner, "mydir")

        assert path.endswith(f"/{owner.user}/mydir")

    def test_creating_makes_the_directory_at_that_path(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        assert location.path == driver.fileset_path(owner, "mydir")
        assert location.name == "mydir"
        assert location.owner == owner
        assert self.probe(location).exists

    def test_the_directory_belongs_to_the_owner(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        assert self.probe(location).uid == owner.uid

    def test_the_directory_gets_the_configured_mode(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        assert self.probe(location).mode == 0o700

    def test_creating_twice_yields_the_same_fileset(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        first = driver.create_fileset(owner, "mydir", 1024)
        second = driver.create_fileset(owner, "mydir", 1024)

        assert first == second
        assert self.probe(second).exists

    @pytest.mark.parametrize("name", ["../escape", "with/slash", "", ".hidden"])
    def test_a_name_that_is_not_a_fileset_name_is_refused(
        self, driver: StorageDriver, owner: Owner, name: str
    ) -> None:
        with pytest.raises(StashError):
            driver.create_fileset(owner, name, 1024)

    def test_setting_a_quota_is_accepted_whether_or_not_it_is_enforced(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        driver.set_fileset_quota(location, 2048)

        assert self.probe(location).exists

    def test_deleting_removes_the_directory_and_its_contents(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)
        self.seed_file(location, "payload")

        driver.delete_fileset(location)

        assert not self.probe(location).exists

    def test_deleting_something_already_gone_is_success(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)
        driver.delete_fileset(location)

        driver.delete_fileset(location)

        assert not self.probe(location).exists

    def test_a_location_outside_the_storage_is_refused(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        outside = FilesetLocation(
            storage_id=driver.storage_id, name="mydir", owner=owner, path="/etc/stash"
        )

        with pytest.raises(StashError):
            driver.delete_fileset(outside)

    def test_two_users_get_separate_directories(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        other = Owner(user="someone-else", uid=owner.uid, gid=owner.gid)

        mine = driver.create_fileset(owner, "mydir", 1024)
        theirs = driver.create_fileset(other, "mydir", 1024)

        assert mine.path != theirs.path

    def test_a_path_resolves_inside_the_storage(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        resolved = driver.resolve("/myuser/mydirectory")

        assert resolved.storage_id == driver.storage_id
        assert resolved.relative == "/myuser/mydirectory"
        assert resolved.absolute.endswith("/myuser/mydirectory")

    @pytest.mark.parametrize("path", ["/../etc", "/a/../../b", "relative", "/a//b"])
    def test_a_path_that_could_leave_the_storage_is_refused(
        self, driver: StorageDriver, path: str
    ) -> None:
        with pytest.raises(StashError):
            driver.resolve(path)

    def test_a_directory_the_owner_may_write_into_says_so(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        """A flush target has to be writable by the user it is flushed for."""
        location = driver.create_fileset(owner, "mydir", 1024)

        found = driver.stat(driver.resolve(self.relative_of(location)), owner=owner)

        assert found.exists and found.is_dir
        assert found.writable

    def test_what_is_not_there_is_not_writable(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        found = driver.stat(driver.resolve("/nothing/here"), owner=owner)

        assert not found.exists
        assert not found.writable

    def test_a_fileset_reports_what_it_actually_holds(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)
        self.seed_file(location, "payload")

        usage = driver.fileset_usage(location)

        assert usage.used_bytes > 0
        assert usage.file_count == 1

    def test_an_empty_fileset_holds_nothing(self, driver: StorageDriver, owner: Owner) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        usage = driver.fileset_usage(location)

        assert usage.file_count == 0

    def test_a_fileset_that_is_gone_reports_nothing_rather_than_failing(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)
        driver.delete_fileset(location)

        usage = driver.fileset_usage(location)

        assert usage.used_bytes == 0
        assert usage.file_count == 0

    def test_the_storage_reports_what_it_holds_in_total(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        first = driver.create_fileset(owner, "mydir", 1024)
        self.seed_file(first, "payload")
        second = driver.create_fileset(owner, "other", 1024)
        self.seed_file(second, "payload")

        usage = driver.storage_usage()

        assert usage.used_bytes >= driver.fileset_usage(first).used_bytes
        assert usage.file_count >= 2

    def test_a_fileset_can_be_addressed_as_a_transfer_endpoint(
        self, driver: StorageDriver, owner: Owner
    ) -> None:
        location = driver.create_fileset(owner, "mydir", 1024)

        endpoint = driver.endpoint(location.path)

        assert endpoint.path == location.path
