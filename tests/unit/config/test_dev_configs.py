"""The configs in dev/ are the ones a developer starts from; they must stay valid."""

from pathlib import Path

import pytest

from stashd.config.bootstrap import DaemonRole, load_bootstrap_config
from stashd.config.cluster import StorageRole, load_cluster_config

DEV = Path(__file__).parents[3] / "dev"


def test_controller_dev_config_is_valid() -> None:
    config = load_bootstrap_config(DEV / "controller.yaml")

    assert config.node.role is DaemonRole.CONTROLLER
    assert config.server.port == 8000
    assert config.cluster_config == DEV / "cluster.yaml"
    assert config.cache_dir == DEV / "var/controller"


def test_storage_daemon_dev_config_is_valid() -> None:
    config = load_bootstrap_config(DEV / "daemon-hot1.yaml")

    assert config.node.role is DaemonRole.STORAGE
    assert config.node.storages == ["HOT1", "LOC2HOT"]
    assert config.server.port == 8001


def test_dev_cluster_template_is_valid(tmp_path: Path) -> None:
    rendered = tmp_path / "cluster.yaml"
    template = (DEV / "cluster.yaml.in").read_text()
    rendered.write_text(template.replace("@STORAGE@", str(tmp_path / "storage")))

    config = load_cluster_config(rendered)

    assert [storage.id for storage in config.storages] == ["HOT1", "LOC2HOT"]
    assert config.storage("HOT1").has_role(StorageRole.SOURCE)
    assert config.storage("LOC2HOT").root == tmp_path / "storage/loc2hot"


def test_dev_storage_roots_stay_where_the_render_puts_them(tmp_path: Path) -> None:
    rendered = tmp_path / "cluster.yaml"
    rendered.write_text(
        (DEV / "cluster.yaml.in").read_text().replace("@STORAGE@", str(tmp_path / "storage"))
    )

    for storage in load_cluster_config(rendered).storages:
        assert (tmp_path / "storage") in storage.root.parents


@pytest.mark.parametrize(
    "name",
    [
        "controller.yaml",
        "daemon-hot1.yaml",
        "cluster.yaml.in",
        "controller-e2e.yaml",
        "daemon-e2e.yaml",
    ],
)
def test_dev_configs_exist(name: str) -> None:
    assert (DEV / name).is_file()
