"""The stashd entry point. One binary; ``self.role`` decides what it becomes."""

from pathlib import Path
from typing import Annotated

import typer
from fastapi import FastAPI

from stashd import __version__
from stashd.api.app import create_app
from stashd.config.bootstrap import BootstrapConfig, Server, load_bootstrap_config
from stashd.config.cluster import ClusterConfig, load_cluster_config
from stashd.config.errors import ConfigError
from stashd.obs.logging import configure_logging

CONFIG_ERROR = 2

app = typer.Typer(add_completion=False, help="STASH controller and storage daemon.")


def _serve(application: FastAPI, server: Server) -> None:  # pragma: no cover - binds a socket
    import uvicorn

    uvicorn.run(
        application,
        host=server.host,
        port=server.port,
        ssl_certfile=server.tls_cert,
        ssl_keyfile=server.tls_key,
    )


def _load_cluster(config: BootstrapConfig) -> ClusterConfig | None:
    """A controller owns the cluster config; a storage daemon fetches it later."""
    if config.cluster_config is None:
        return None
    return load_cluster_config(config.cluster_config)


@app.command()
def main(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Bootstrap config file.")
    ] = Path("/etc/stash/stashd.yaml"),
    version: Annotated[
        bool, typer.Option("--version", help="Print the version and exit.")
    ] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit
    try:
        bootstrap = load_bootstrap_config(config)
        cluster = _load_cluster(bootstrap)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    configure_logging(bootstrap.logging.level, bootstrap.logging.format)
    _serve(create_app(bootstrap, cluster), bootstrap.server)
