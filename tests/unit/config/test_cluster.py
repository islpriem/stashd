"""Cluster config loading and validation."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from stashd.config.cluster import StorageRole, load_cluster_config
from stashd.config.errors import ConfigError

WriteCluster = Callable[[dict[str, Any]], Path]
Cluster = dict[str, Any]


class TestValidConfig:
    def test_valid_config_loads(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        config = load_cluster_config(write_cluster(valid_cluster))

        assert config.revision == 42
        assert [loc.id for loc in config.locations] == ["LOC1", "LOC2"]
        assert config.storage("LOC2HOT").capacity_bytes == 500 * 1024**4
        assert config.storage("LOC2HOT").default_user_allocation_limit_bytes == 100 * 1024**3
        assert config.storage("HOT1").roles == [StorageRole.SOURCE]
        assert config.limits.user_total_cache_allocation_bytes == 250 * 1024**3
        assert config.limits.concurrency.global_ == 20
        assert config.limits.bandwidth.per_route_aggregate_bytes_per_s == 250_000_000
        assert config.scheduling.interval == timedelta(seconds=5)
        assert config.scheduling.fairshare.half_life == timedelta(days=7)
        assert config.transfer.nominal_throughput.routes["HOT1->LOC2HOT"] == 120_000_000
        assert config.retention.transfers == timedelta(days=90)
        assert config.auth.admin_uids == [0]
        assert config.auth.admin_gids == ["hpc-admin"]

    def test_fileset_prefix_defaults_to_root(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        del valid_cluster["storages"][1]["fileset_prefix"]

        config = load_cluster_config(write_cluster(valid_cluster))

        assert config.storage("LOC2HOT").fileset_prefix == Path("/cache/loc2")

    def test_content_hash_is_stable_and_changes_with_content(
        self, write_cluster: WriteCluster, valid_cluster: Cluster, tmp_path: Path
    ) -> None:
        original = write_cluster(valid_cluster)
        first = load_cluster_config(original)
        reformatted = tmp_path / "again.yaml"
        reformatted.write_text(original.read_text() + "\n# a comment\n")
        valid_cluster["revision"] = 43

        assert first.content_hash == load_cluster_config(reformatted).content_hash
        assert (
            first.content_hash != load_cluster_config(write_cluster(valid_cluster)).content_hash
        )

    def test_a_daemon_is_addressable_by_id(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        config = load_cluster_config(write_cluster(valid_cluster))

        assert config.daemon("loc2hot").url == "http://127.0.0.1:8002"
        assert config.daemon("loc2hot").host == "stash-loc2.example.org"
        assert config.daemon_for("LOC2HOT").id == "loc2hot"

    def test_the_ssh_host_defaults_to_the_one_in_the_url(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        config = load_cluster_config(write_cluster(valid_cluster))

        assert config.daemon("hot1").host == "127.0.0.1"

    def test_the_fileset_mode_defaults_to_0700_and_is_per_storage(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["fileset_mode"] = "0750"

        config = load_cluster_config(write_cluster(valid_cluster))

        assert config.storage("HOT1").fileset_mode == 0o700
        assert config.storage("LOC2HOT").fileset_mode == 0o750

    def test_storages_are_addressable_by_id(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        config = load_cluster_config(write_cluster(valid_cluster))

        assert [storage.id for storage in config.storages] == ["HOT1", "LOC2HOT"]
        assert [storage.id for storage in config.cache_storages()] == ["LOC2HOT"]


class TestLookup:
    def test_an_unknown_storage_id_raises(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        config = load_cluster_config(write_cluster(valid_cluster))

        with pytest.raises(KeyError):
            config.storage("NOPE")

        with pytest.raises(KeyError):
            config.daemon("nope")


class TestRejection:
    def _problems(self, write_cluster: WriteCluster, cluster: Cluster) -> str:
        with pytest.raises(ConfigError) as excinfo:
            load_cluster_config(write_cluster(cluster))
        return str(excinfo.value)

    def test_duplicate_storage_id(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["id"] = "HOT1"

        assert "duplicate storage id 'HOT1'" in self._problems(write_cluster, valid_cluster)

    def test_duplicate_location_id(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["locations"][1]["id"] = "LOC1"

        assert "duplicate location id 'LOC1'" in self._problems(write_cluster, valid_cluster)

    def test_unknown_location_reference(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][0]["location"] = "NOWHERE"

        assert "unknown location 'NOWHERE'" in self._problems(write_cluster, valid_cluster)

    def test_cache_storage_without_capacity(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        del valid_cluster["storages"][1]["capacity"]

        assert "needs a capacity" in self._problems(write_cluster, valid_cluster)

    def test_nested_roots_on_one_daemon(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["daemon"] = "hot1"
        valid_cluster["storages"][1]["root"] = "/gpfs/hot1/cache"

        assert "nests inside" in self._problems(write_cluster, valid_cluster)

    def test_identical_roots_on_different_daemons_are_allowed(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["root"] = "/gpfs/hot1"
        valid_cluster["storages"][1]["fileset_prefix"] = "/gpfs/hot1"

        assert (
            load_cluster_config(write_cluster(valid_cluster)).storage("LOC2HOT").daemon
            == "loc2hot"
        )

    def test_route_referencing_unknown_storage(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["transfer"]["nominal_throughput"]["routes"] = {"HOT1->GONE": "120MB/s"}

        assert "unknown storage 'GONE'" in self._problems(write_cluster, valid_cluster)

    def test_malformed_route_key(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["transfer"]["nominal_throughput"]["routes"] = {
            "HOT1 to LOC2HOT": "120MB/s"
        }

        assert "not a route" in self._problems(write_cluster, valid_cluster)

    def test_bandwidth_minimum_above_maximum(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["limits"]["bandwidth"]["min_per_transfer"] = "900Mbit"

        assert "min_per_transfer" in self._problems(write_cluster, valid_cluster)

    def test_unparseable_size(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["capacity"] = "500Terabytes"

        assert "invalid size" in self._problems(write_cluster, valid_cluster)

    def test_unparseable_duration(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["scheduling"]["interval"] = "5 seconds"

        assert "invalid duration" in self._problems(write_cluster, valid_cluster)

    def test_unknown_key_is_rejected(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][0]["tierr"] = "hot"

        assert "tierr" in self._problems(write_cluster, valid_cluster)

    @pytest.mark.parametrize("storage_id", ["hot1", "1HOT", "HOT 1", ""])
    def test_storage_id_must_be_an_uppercase_slug(
        self, write_cluster: WriteCluster, valid_cluster: Cluster, storage_id: str
    ) -> None:
        valid_cluster["storages"][0]["id"] = storage_id

        assert "storages.0.id" in self._problems(write_cluster, valid_cluster)

    @pytest.mark.parametrize("fill_limit", [0.0, 1.5, -0.1])
    def test_fill_limit_out_of_range(
        self, write_cluster: WriteCluster, valid_cluster: Cluster, fill_limit: float
    ) -> None:
        valid_cluster["storages"][1]["fill_limit"] = fill_limit

        assert "fill_limit" in self._problems(write_cluster, valid_cluster)

    def test_a_storage_whose_daemon_is_not_declared(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["daemon"] = "nobody"

        assert "unknown daemon 'nobody'" in self._problems(write_cluster, valid_cluster)

    def test_duplicate_daemon_id(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["daemons"][1]["id"] = "hot1"

        assert "duplicate daemon id 'hot1'" in self._problems(write_cluster, valid_cluster)

    @pytest.mark.parametrize("mode", ["0999", "07777", ["0700"], "rwx"])
    def test_a_mode_that_is_not_an_octal_permission_is_refused(
        self, write_cluster: WriteCluster, valid_cluster: Cluster, mode: object
    ) -> None:
        valid_cluster["storages"][0]["fileset_mode"] = mode

        assert "fileset_mode" in self._problems(write_cluster, valid_cluster)

    def test_unknown_retry_class(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["transfer"]["retries"]["retry_on"] = ["network", "sunspots"]

        assert "retry_on" in self._problems(write_cluster, valid_cluster)

    def test_unknown_driver(self, write_cluster: WriteCluster, valid_cluster: Cluster) -> None:
        valid_cluster["storages"][0]["driver"] = "cephfs"

        assert "driver" in self._problems(write_cluster, valid_cluster)

    def test_empty_roles(self, write_cluster: WriteCluster, valid_cluster: Cluster) -> None:
        valid_cluster["storages"][0]["roles"] = []

        assert "roles" in self._problems(write_cluster, valid_cluster)

    def test_relative_root(self, write_cluster: WriteCluster, valid_cluster: Cluster) -> None:
        valid_cluster["storages"][0]["root"] = "gpfs/hot1"

        assert "absolute" in self._problems(write_cluster, valid_cluster)

    def test_allocation_headroom_below_one(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["scheduling"]["allocation_headroom"] = 0.9

        assert "allocation_headroom" in self._problems(write_cluster, valid_cluster)

    def test_every_problem_is_reported_at_once(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][0]["location"] = "NOWHERE"
        valid_cluster["storages"][1]["daemon"] = "hot1"
        valid_cluster["storages"][1]["root"] = "/gpfs/hot1/cache"
        del valid_cluster["storages"][1]["capacity"]

        problems = self._problems(write_cluster, valid_cluster)

        assert "unknown location 'NOWHERE'" in problems
        assert "nests inside" in problems
        assert "needs a capacity" in problems

    def test_a_size_of_the_wrong_type(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["storages"][1]["capacity"] = ["500Ti"]

        assert "invalid size" in self._problems(write_cluster, valid_cluster)

    def test_a_rate_of_the_wrong_type(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["limits"]["bandwidth"]["max_per_transfer"] = {"value": 5}

        assert "invalid rate" in self._problems(write_cluster, valid_cluster)

    def test_a_duration_given_as_a_number(
        self, write_cluster: WriteCluster, valid_cluster: Cluster
    ) -> None:
        valid_cluster["scheduling"]["interval"] = 5

        assert "invalid duration" in self._problems(write_cluster, valid_cluster)

    def test_an_empty_file_reports_the_missing_sections(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text("")

        with pytest.raises(ConfigError, match="revision"):
            load_cluster_config(path)

    def test_missing_file_is_a_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            load_cluster_config(tmp_path / "absent.yaml")

    def test_malformed_yaml_is_a_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text("revision: [unclosed\n")

        with pytest.raises(ConfigError, match="not valid YAML"):
            load_cluster_config(path)

    def test_yaml_that_is_not_a_mapping_is_a_config_error(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text("- a\n- b\n")

        with pytest.raises(ConfigError, match="mapping"):
            load_cluster_config(path)
