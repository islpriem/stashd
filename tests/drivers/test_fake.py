"""The fake driver passes the same suite, on the other side of every capability."""

import pytest

from stashd.domain.storage import FilesetLocation, Owner
from stashd.drivers.fake import FakeDriver
from tests.drivers.conformance import DriverConformance, Probe


class TestFakeDriver(DriverConformance):
    @pytest.fixture
    def driver(self) -> FakeDriver:
        self.instance = FakeDriver()
        return self.instance

    @pytest.fixture
    def owner(self) -> Owner:
        return Owner(user="mmustermann", uid=1000, gid=1000)

    def probe(self, location: FilesetLocation) -> Probe:
        fileset = self.instance.filesets.get(location.path)
        if fileset is None:
            return Probe(exists=False)
        return Probe(
            exists=True,
            uid=fileset.owner.uid,
            mode=fileset.mode,
            entries=tuple(sorted(fileset.files)),
        )

    def seed_file(self, location: FilesetLocation, name: str) -> None:
        self.instance.filesets[location.path].files[name] = 7


class TestFakeDriverExtras:
    def test_it_declares_the_capabilities_posix_lacks(self) -> None:
        assert FakeDriver().capabilities.native_quota is True

    def test_a_quota_is_recorded_when_the_driver_can_enforce_one(self) -> None:
        driver = FakeDriver()
        owner = Owner(user="mmustermann", uid=1000, gid=1000)
        location = driver.create_fileset(owner, "mydir", 1024)

        driver.set_fileset_quota(location, 4096)

        assert driver.filesets[location.path].quota_bytes == 4096

    def test_a_fileset_of_another_user_is_never_adopted(self) -> None:
        driver = FakeDriver()
        mine = Owner(user="shared", uid=1000, gid=1000)
        theirs = Owner(user="shared", uid=1001, gid=1001)
        driver.create_fileset(mine, "mydir", 1024)

        with pytest.raises(Exception, match="belongs to"):
            driver.create_fileset(theirs, "mydir", 1024)

    def test_a_failure_can_be_armed_for_one_call(self) -> None:
        driver = FakeDriver()
        owner = Owner(user="mmustermann", uid=1000, gid=1000)
        driver.fail_on("create_fileset", RuntimeError("disk on fire"))

        with pytest.raises(RuntimeError):
            driver.create_fileset(owner, "mydir", 1024)

        assert driver.create_fileset(owner, "mydir", 1024).name == "mydir"
