"""The stashd entry point: one binary, both roles."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from typer.testing import CliRunner

from stashd import __version__, cli
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


def test_storage_daemon_starts_without_a_cluster_config(
    monkeypatch: Any, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
) -> None:
    served = serve_recorder(monkeypatch)

    result = runner.invoke(cli.app, ["--config", str(write_bootstrap(storage_yaml))])

    assert result.exit_code == 0, result.output
    assert served.app is not None
    assert served.app.state.cluster is None
    assert served.app.state.bootstrap.node.daemon_id == "hot1"


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


def test_logging_is_configured_from_the_bootstrap_config(
    monkeypatch: Any, write_bootstrap: WriteConfig, storage_yaml: dict[str, Any]
) -> None:
    serve_recorder(monkeypatch)
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
