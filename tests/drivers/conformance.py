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
