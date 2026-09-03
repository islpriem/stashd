"""The stashd entry point: one binary, both roles."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from typer.testing import CliRunner

from stashd import __version__, cli
from stashd.auth.munge import MungeAuthProvider
from stashd.config.bootstrap import LogFormat, LogLevel, Server
from tests.conftest import WriteConfig

runner = CliRunner()


class Served:
    """Stands in for uvicorn: records what would have been served."""

    def __init__(self) -> None:
        self.app: FastAPI | None = None
        self.server: Server | None = None

    def __call__(self, app: FastAPI, server: Server) -> None:
        self.app = app
        self.server = server


def serve_recorder(monkeypatch: Any) -> Served:
    served = Served()
    monkeypatch.setattr(cli, "_serve", served)
    return served


class FakeSource:
    """Stands in for the controller a storage daemon fetches its config from."""

    def __init__(self, document: object | Exception) -> None:
        self.answer = document

    def fetch(self) -> object:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def served(
    monkeypatch: Any,
    write_cluster: WriteConfig,
    cluster: dict[str, Any],
    tmp_path: Any,
    failure: Exception | None = None,
) -> None:
    """Every storage in the config gets a real root, and the controller serves it."""
    from stashd.config.cluster import load_cluster_document

    for storage in cluster["storages"]:
        root = tmp_path / storage["id"].lower()
        root.mkdir(exist_ok=True)
        storage["root"] = str(root)
        storage.pop("fileset_prefix", None)
    document = load_cluster_document(write_cluster(cluster))
    monkeypatch.setattr(cli, "_config_source", lambda config: FakeSource(failure or document))


def daemon_config(storage_yaml: dict[str, Any], tmp_path: Any) -> dict[str, Any]:
    (tmp_path / "peer-token").write_text("s3cret\n")
    (tmp_path / "peer-token").chmod(0o600)
    return {
        **storage_yaml,
        "peer_token_file": "peer-token",
        "identity": "current",
        "worker_pool_size": 8,
    }


def test_controller_starts_from_its_config(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    controller_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
) -> None:
    write_cluster(valid_cluster)
    served = serve_recorder(monkeypatch)

    result = runner.invoke(cli.app, ["--config", str(write_bootstrap(controller_yaml))])

    assert result.exit_code == 0, result.output
    assert served.app is not None
    assert served.app.state.cluster is not None
    assert served.app.state.cluster.revision == 42
    assert served.server is not None
    assert (served.server.host, served.server.port) == ("0.0.0.0", 8443)


def test_a_storage_daemon_starts_from_the_config_it_fetched(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    storage_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
    tmp_path: Any,
) -> None:
    served(monkeypatch, write_cluster, valid_cluster, tmp_path)
    recorder = serve_recorder(monkeypatch)

    result = runner.invoke(
        cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
    )

    assert result.exit_code == 0, result.output
    assert recorder.app is not None
    assert recorder.app.state.cluster.revision == 42
    assert recorder.app.state.config_degraded is False
    assert (tmp_path / "var/hot1/cluster.yaml").is_file(), "and cached what it got"


def test_an_invalid_cluster_config_stops_the_controller(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    controller_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
) -> None:
    del valid_cluster["storages"][1]["capacity"]
    write_cluster(valid_cluster)
    served = serve_recorder(monkeypatch)

    result = runner.invoke(cli.app, ["--config", str(write_bootstrap(controller_yaml))])

    assert result.exit_code == 2
    assert "needs a capacity" in result.output
    assert "Traceback" not in result.output
    assert served.app is None


def test_a_missing_config_file_exits_with_a_message(monkeypatch: Any, tmp_path: Path) -> None:
    served = serve_recorder(monkeypatch)

    result = runner.invoke(cli.app, ["--config", str(tmp_path / "absent.yaml")])

    assert result.exit_code == 2
    assert "does not exist" in result.output
    assert "Traceback" not in result.output
    assert served.app is None


def test_the_controller_gets_a_database_and_an_auth_provider(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    controller_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
) -> None:
    write_cluster(valid_cluster)
    served = serve_recorder(monkeypatch)

    result = runner.invoke(cli.app, ["--config", str(write_bootstrap(controller_yaml))])

    assert result.exit_code == 0, result.output
    assert served.app is not None
    assert served.app.state.sessions is not None
    assert isinstance(served.app.state.auth, MungeAuthProvider)


def test_a_storage_daemon_has_no_database_and_no_munge(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    storage_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
    tmp_path: Any,
) -> None:
    served(monkeypatch, write_cluster, valid_cluster, tmp_path)
    recorder = serve_recorder(monkeypatch)

    runner.invoke(
        cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
    )

    assert recorder.app is not None
    assert recorder.app.state.sessions is None
    assert recorder.app.state.auth is None


def test_logging_is_configured_from_the_bootstrap_config(
    monkeypatch: Any,
    write_bootstrap: WriteConfig,
    write_cluster: WriteConfig,
    storage_yaml: dict[str, Any],
    valid_cluster: dict[str, Any],
    tmp_path: Any,
) -> None:
    served(monkeypatch, write_cluster, valid_cluster, tmp_path)
    serve_recorder(monkeypatch)
    storage_yaml = daemon_config(storage_yaml, tmp_path)
    storage_yaml["logging"] = {"level": "WARNING", "format": "console"}
    configured: list[tuple[LogLevel, LogFormat]] = []
    monkeypatch.setattr(
        cli,
        "configure_logging",
        lambda level, log_format: configured.append((level, log_format)),
    )

    result = runner.invoke(cli.app, ["--config", str(write_bootstrap(storage_yaml))])

    assert result.exit_code == 0, result.output
    assert configured == [(LogLevel.WARNING, LogFormat.CONSOLE)]


def test_version_is_printed_without_a_config() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


class TestStorageDaemonStartup:
    def test_a_driver_is_built_for_each_storage_the_daemon_serves(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        served(monkeypatch, write_cluster, valid_cluster, tmp_path)
        recorder = serve_recorder(monkeypatch)

        result = runner.invoke(
            cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
        )

        assert result.exit_code == 0, result.output
        assert recorder.app is not None
        assert set(recorder.app.state.drivers) == {"HOT1"}
        assert recorder.app.state.peer_auth is not None
        assert recorder.app.state.store is None

    def test_a_storage_root_that_is_missing_stops_the_daemon(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        served(monkeypatch, write_cluster, valid_cluster, tmp_path)
        (tmp_path / "hot1").rmdir()
        recorder = serve_recorder(monkeypatch)

        result = runner.invoke(
            cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
        )

        assert result.exit_code == 2
        assert "does not exist" in result.output
        assert recorder.app is None

    def test_an_unreachable_controller_starts_the_daemon_from_its_cache(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        from stashd.config.distribution import ConfigUnavailable

        served(monkeypatch, write_cluster, valid_cluster, tmp_path)
        recorder = serve_recorder(monkeypatch)
        bootstrap = write_bootstrap(daemon_config(storage_yaml, tmp_path))
        runner.invoke(cli.app, ["--config", str(bootstrap)])

        served(monkeypatch, write_cluster, valid_cluster, tmp_path, ConfigUnavailable("down"))
        result = runner.invoke(cli.app, ["--config", str(bootstrap)])

        assert result.exit_code == 0, result.output
        assert recorder.app is not None
        assert recorder.app.state.config_degraded is True

    def test_a_daemon_smaller_than_the_cluster_may_dispatch_refuses_to_start(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        served(monkeypatch, write_cluster, valid_cluster, tmp_path)
        recorder = serve_recorder(monkeypatch)
        undersized = {**daemon_config(storage_yaml, tmp_path), "worker_pool_size": 1}

        result = runner.invoke(cli.app, ["--config", str(write_bootstrap(undersized))])

        assert result.exit_code == 2
        assert "concurrency.per_storage" in result.output
        assert recorder.app is None

    def test_the_daemon_runs_transfers_beside_its_requests(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        served(monkeypatch, write_cluster, valid_cluster, tmp_path)
        recorder = serve_recorder(monkeypatch)

        runner.invoke(
            cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
        )

        assert recorder.app is not None
        assert recorder.app.state.runner is not None

    def test_without_a_cache_an_unreachable_controller_stops_the_daemon(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        storage_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        from stashd.config.distribution import ConfigUnavailable

        served(monkeypatch, write_cluster, valid_cluster, tmp_path, ConfigUnavailable("down"))
        recorder = serve_recorder(monkeypatch)

        result = runner.invoke(
            cli.app, ["--config", str(write_bootstrap(daemon_config(storage_yaml, tmp_path)))]
        )

        assert result.exit_code == 2
        assert "no cached cluster config" in result.output
        assert recorder.app is None

    def test_the_controller_can_reach_its_daemons(
        self,
        monkeypatch: Any,
        write_bootstrap: WriteConfig,
        write_cluster: WriteConfig,
        controller_yaml: dict[str, Any],
        valid_cluster: dict[str, Any],
        tmp_path: Any,
    ) -> None:
        write_cluster(valid_cluster)
        (tmp_path / "peer-token").write_text("s3cret\n")
        (tmp_path / "peer-token").chmod(0o600)
        controller_yaml["peer_token_file"] = "peer-token"
        recorder = serve_recorder(monkeypatch)

        result = runner.invoke(cli.app, ["--config", str(write_bootstrap(controller_yaml))])

        assert result.exit_code == 0, result.output
        assert recorder.app is not None
        assert recorder.app.state.store is not None
        assert recorder.app.state.document is not None
