"""Configuration fixtures shared by the whole suite.

Each fixture hands out a fresh mutable copy, so a test can break exactly one thing.
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from stashd.config.bootstrap import BootstrapConfig, load_bootstrap_config
from stashd.config.cluster import ClusterConfig, load_cluster_config

VALID_CLUSTER: dict[str, Any] = {
    "revision": 42,
    "locations": [
        {"id": "LOC1", "name": "Site 1"},
        {"id": "LOC2", "name": "Site 2"},
    ],
    "daemons": [
        {"id": "hot1", "url": "http://127.0.0.1:8001"},
        {"id": "loc2hot", "url": "http://127.0.0.1:8002", "host": "stash-loc2.example.org"},
    ],
    "storages": [
        {
            "id": "HOT1",
            "location": "LOC1",
            "roles": ["source"],
            "tier": "hot",
            "driver": "posix",
            "root": "/gpfs/hot1",
            "daemon": "hot1",
        },
        {
            "id": "LOC2HOT",
            "location": "LOC2",
            "roles": ["cache"],
            "tier": "hot",
            "driver": "posix",
            "root": "/cache/loc2",
            "fileset_prefix": "/cache/loc2",
            "capacity": "500Ti",
            "fill_limit": 0.95,
            "default_user_allocation_limit": "100Gi",
            "daemon": "loc2hot",
            "fileset_mode": "0700",
        },
    ],
    "limits": {
        "user_total_cache_allocation": "250Gi",
        "max_filesets_per_user": 50,
        "queued_transfers_per_user": 50,
        "concurrency": {"global": 20, "per_storage": 8, "per_user": 2, "per_route": 4},
        "bandwidth": {
            "per_route_aggregate": "2Gbit",
            "max_per_transfer": "800Mbit",
            "min_per_transfer": "50Mbit",
        },
    },
    "scheduling": {
        "interval": "5s",
        "fairshare": {"half_life": "7d", "points_per_gib": 1.0},
        "allocation_headroom": 1.05,
    },
    "transfer": {
        "engine": "rsync",
        "retries": {"count": 2, "backoff": "60s", "retry_on": ["network", "timeout"]},
        "progress_poll_interval": "15s",
        "nominal_throughput": {"default": "200MB/s", "routes": {"HOT1->LOC2HOT": "120MB/s"}},
    },
    "auth": {
        "cli": "munge",
        "munge_socket": "/run/munge/munge.socket.2",
        "peer": "token",
        "admin_uids": [0],
        "admin_gids": ["hpc-admin"],
    },
    "retention": {"transfers": "90d", "audit": "365d"},
}

CONTROLLER_BOOTSTRAP: dict[str, Any] = {
    "self": {"daemon_id": "controller", "role": "controller"},
    "server": {"host": "0.0.0.0", "port": 8443},
    "database": {"url": "postgresql+psycopg://stash@localhost/stash"},
    "logging": {"level": "INFO", "format": "json"},
    "cache_dir": "var/controller",
    "cluster_config": "cluster.yaml",
}

STORAGE_BOOTSTRAP: dict[str, Any] = {
    "self": {"daemon_id": "hot1", "role": "storage", "storages": ["HOT1"]},
    "controller": {"url": "https://stash-controller:8443", "token_file": "peer-token"},
    "server": {"host": "0.0.0.0", "port": 8444},
    "logging": {"level": "INFO", "format": "json"},
    "cache_dir": "var/hot1",
}

WriteConfig = Callable[[dict[str, Any]], Path]


@pytest.fixture
def valid_cluster() -> dict[str, Any]:
    return copy.deepcopy(VALID_CLUSTER)


@pytest.fixture
def controller_yaml() -> dict[str, Any]:
    return copy.deepcopy(CONTROLLER_BOOTSTRAP)


@pytest.fixture
def storage_yaml() -> dict[str, Any]:
    return copy.deepcopy(STORAGE_BOOTSTRAP)


@pytest.fixture
def write_cluster(tmp_path: Path) -> WriteConfig:
    def write(data: dict[str, Any]) -> Path:
        path = tmp_path / "cluster.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    return write


@pytest.fixture
def write_bootstrap(tmp_path: Path) -> WriteConfig:
    def write(data: dict[str, Any]) -> Path:
        path = tmp_path / "stashd.yaml"
        path.write_text(yaml.safe_dump(data))
        return path

    return write


@pytest.fixture
def cluster_config(write_cluster: WriteConfig, valid_cluster: dict[str, Any]) -> ClusterConfig:
    return load_cluster_config(write_cluster(valid_cluster))


@pytest.fixture
def controller_bootstrap(
    write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
) -> BootstrapConfig:
    return load_bootstrap_config(write_bootstrap(controller_yaml))


@pytest.fixture
def storage_bootstrap(
    write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
) -> BootstrapConfig:
    return load_bootstrap_config(write_bootstrap(storage_yaml))
