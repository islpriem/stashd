"""The stashd entry point. One binary; ``self.role`` decides what it becomes."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx2
import typer
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stashd import __version__
from stashd.api.app import create_app
from stashd.auth.munge import MungeAuthProvider
from stashd.auth.provider import AuthProvider
from stashd.auth.system import SystemIdentityLookup
from stashd.auth.token import TokenAuthProvider
from stashd.clients.filesets import FilesetStore, HttpFilesetStore
from stashd.config.bootstrap import (
    BootstrapConfig,
    IdentityKind,
    Server,
    load_bootstrap_config,
)
from stashd.config.cluster import ClusterConfig, load_cluster_config
from stashd.config.errors import ConfigError
from stashd.db import create_engine, session_factory
from stashd.drivers.base import StorageDriver
from stashd.drivers.factory import build_drivers
from stashd.identity.base import Identity
from stashd.identity.current import CurrentUserIdentity
from stashd.identity.sudo import SudoIdentity
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


@dataclass
class Parts:
    """Everything a role needs, assembled before the server starts."""

    sessions: async_sessionmaker[AsyncSession] | None = None
    auth: AuthProvider | None = None
    peer_auth: TokenAuthProvider | None = None
    drivers: dict[str, StorageDriver] | None = None
    store: FilesetStore | None = None


def _identity(kind: IdentityKind) -> Identity:
    """Sudo for now; development runs as its own user."""
    return SudoIdentity() if kind is IdentityKind.SUDO else CurrentUserIdentity()


def _peer_token(config: BootstrapConfig) -> TokenAuthProvider | None:
    if config.peer_token_file is None:
        return None
    return TokenAuthProvider.from_file(config.peer_token_file)


def _assemble(config: BootstrapConfig, cluster: ClusterConfig) -> Parts:
    parts = Parts(peer_auth=_peer_token(config))
    if config.is_controller:
        if config.database is not None:
            parts.sessions = session_factory(create_engine(config.database.url))
        parts.auth = MungeAuthProvider(cluster.auth.munge_socket, SystemIdentityLookup())
        if config.peer_token_file is not None:
            parts.store = HttpFilesetStore(
                cluster, config.peer_token_file.read_text().strip(), httpx2.AsyncClient()
            )
        return parts
    parts.drivers = build_drivers(
        cluster,
        config.node.storages,
        _identity(config.identity),
        daemon_id=config.node.daemon_id,
        config_path=config.cluster_config,
    )
    return parts


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
    try:
        parts = _assemble(bootstrap, cluster) if cluster is not None else Parts()
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    _serve(
        create_app(
            bootstrap,
            cluster,
            auth=parts.auth,
            sessions=parts.sessions,
            peer_auth=parts.peer_auth,
            drivers=parts.drivers,
            store=parts.store,
        ),
        bootstrap.server,
    )
