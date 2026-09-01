"""Building the drivers a storage daemon serves, and refusing to start otherwise."""

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from stashd.config.cluster import ClusterConfig, load_cluster_config
from stashd.config.errors import ConfigError
from stashd.domain.storage import Owner
from stashd.drivers.factory import build_drivers
from stashd.drivers.posix import PosixDriver
from stashd.identity.current import CurrentUserIdentity
from tests.conftest import WriteConfig

Cluster = dict[str, Any]


def config_with_roots(
    valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
) -> ClusterConfig:
    """Give every storage a real directory: the factory refuses one that is not there."""
    for storage in valid_cluster["storages"]:
        root = tmp_path / storage["id"].lower()
        root.mkdir(exist_ok=True)
        storage["root"] = str(root)
        storage.pop("fileset_prefix", None)
    return load_cluster_config(write_cluster(valid_cluster))


class TestBuildDrivers:
    def test_a_driver_is_built_for_every_storage_the_daemon_serves(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)

        drivers = build_drivers(config, ["HOT1"], CurrentUserIdentity())

        assert set(drivers) == {"HOT1"}
        assert isinstance(drivers["HOT1"], PosixDriver)

    def test_the_configured_mode_reaches_the_driver(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        valid_cluster["storages"][1]["fileset_mode"] = "0750"
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)

        driver = build_drivers(config, ["LOC2HOT"], CurrentUserIdentity())["LOC2HOT"]
        owner = Owner(user="tester", uid=os.getuid(), gid=os.getgid())
        location = driver.create_fileset(owner, "mydir", 1024)

        assert stat.S_IMODE(Path(location.path).stat().st_mode) == 0o750

    def test_a_storage_missing_from_the_cluster_config_refuses_the_start(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)

        with pytest.raises(ConfigError, match="NOWHERE"):
            build_drivers(config, ["NOWHERE"], CurrentUserIdentity())

    def test_a_storage_assigned_to_another_daemon_refuses_the_start(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)

        with pytest.raises(ConfigError, match="loc2hot"):
            build_drivers(config, ["LOC2HOT"], CurrentUserIdentity(), daemon_id="hot1")

    def test_a_root_that_does_not_exist_refuses_the_start(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)
        (tmp_path / "hot1").rmdir()

        with pytest.raises(ConfigError, match="does not exist"):
            build_drivers(config, ["HOT1"], CurrentUserIdentity())

    def test_a_root_that_is_a_file_refuses_the_start(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)
        (tmp_path / "hot1").rmdir()
        (tmp_path / "hot1").write_text("not a directory")

        with pytest.raises(ConfigError, match="not a directory"):
            build_drivers(config, ["HOT1"], CurrentUserIdentity())

    def test_every_problem_is_reported_at_once(
        self, valid_cluster: Cluster, write_cluster: WriteConfig, tmp_path: Path
    ) -> None:
        config = config_with_roots(valid_cluster, write_cluster, tmp_path)
        (tmp_path / "hot1").rmdir()

        with pytest.raises(ConfigError) as excinfo:
            build_drivers(config, ["HOT1", "NOWHERE"], CurrentUserIdentity())

        assert len(excinfo.value.problems) == 2
