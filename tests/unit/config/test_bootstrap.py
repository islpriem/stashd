"""Bootstrap config: what a single process needs to start."""

from pathlib import Path
from typing import Any

import pytest

from stashd.config.bootstrap import DaemonRole, load_bootstrap_config
from stashd.config.errors import ConfigError
from tests.conftest import WriteConfig


def problems(write: WriteConfig, data: dict[str, Any]) -> str:
    with pytest.raises(ConfigError) as excinfo:
        load_bootstrap_config(write(data))
    return str(excinfo.value)


class TestRoles:
    def test_controller_config_loads(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        config = load_bootstrap_config(write_bootstrap(controller_yaml))

        assert config.node.role is DaemonRole.CONTROLLER
        assert config.node.daemon_id == "controller"
        assert config.is_controller
        assert config.server.port == 8443
        assert config.database is not None
        assert config.cluster_config is not None

    def test_storage_daemon_config_loads(
        self, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
    ) -> None:
        config = load_bootstrap_config(write_bootstrap(storage_yaml))

        assert config.node.role is DaemonRole.STORAGE
        assert not config.is_controller
        assert config.node.storages == ["HOT1"]
        assert config.controller is not None
        assert config.controller.token_file.name == "peer-token"
        assert config.broker is not None

    def test_unknown_role_is_rejected(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        controller_yaml["self"] = {"daemon_id": "x", "role": "scheduler"}

        assert "role" in problems(write_bootstrap, controller_yaml)


class TestRoleSpecificRequirements:
    def test_controller_needs_a_database(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        del controller_yaml["database"]

        assert "controller needs a database" in problems(write_bootstrap, controller_yaml)

    def test_controller_needs_a_cluster_config(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        del controller_yaml["cluster_config"]

        assert "controller needs a cluster_config" in problems(write_bootstrap, controller_yaml)

    def test_storage_daemon_needs_a_controller_url(
        self, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
    ) -> None:
        del storage_yaml["controller"]

        assert "storage daemon needs a controller" in problems(write_bootstrap, storage_yaml)

    def test_storage_daemon_needs_a_broker(
        self, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
    ) -> None:
        del storage_yaml["broker"]

        assert "storage daemon needs a broker" in problems(write_bootstrap, storage_yaml)

    def test_storage_daemon_needs_at_least_one_storage(
        self, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
    ) -> None:
        storage_yaml["self"] = {"daemon_id": "hot1", "role": "storage", "storages": []}

        assert "storage daemon needs at least one storage" in problems(
            write_bootstrap, storage_yaml
        )

    def test_every_problem_is_reported_at_once(
        self, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
    ) -> None:
        del storage_yaml["broker"]
        del storage_yaml["controller"]

        assert "needs a broker" in problems(write_bootstrap, storage_yaml)
        assert "needs a controller" in problems(write_bootstrap, storage_yaml)


class TestPathsAndDefaults:
    def test_relative_paths_resolve_against_the_config_file(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any], tmp_path: Path
    ) -> None:
        controller_yaml["cache_dir"] = "var/controller"
        controller_yaml["cluster_config"] = "cluster.yaml"

        config = load_bootstrap_config(write_bootstrap(controller_yaml))

        assert config.cache_dir == tmp_path / "var/controller"
        assert config.cluster_config == tmp_path / "cluster.yaml"

    def test_logging_defaults_to_json_at_info(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        del controller_yaml["logging"]

        config = load_bootstrap_config(write_bootstrap(controller_yaml))

        assert config.logging.level == "INFO"
        assert config.logging.format == "json"

    def test_tls_needs_both_certificate_and_key(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        controller_yaml["server"] = {
            "host": "0.0.0.0",
            "port": 8443,
            "tls_cert": "/tls/cert.pem",
        }

        assert "tls_cert and tls_key" in problems(write_bootstrap, controller_yaml)

    def test_unknown_key_is_rejected(
        self, write_bootstrap: WriteConfig, controller_yaml: dict[str, Any]
    ) -> None:
        controller_yaml["cach_dir"] = "/var/lib/stash"

        assert "cach_dir" in problems(write_bootstrap, controller_yaml)
