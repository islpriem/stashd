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
from stashd.clients.config import HttpConfigSource
from stashd.clients.events import EventReporter, HttpEventReporter, NullEventReporter
from stashd.clients.filesets import FilesetStore, HttpFilesetStore
from stashd.clients.registration import Announcement, HttpRegistrar
from stashd.clients.transfers import HttpTransferDispatcher, TransferDispatcher
from stashd.config.bootstrap import (
    BootstrapConfig,
    IdentityKind,
    Server,
    load_bootstrap_config,
)
from stashd.config.cluster import ConfigDocument, load_cluster_document
from stashd.config.distribution import (
    ConfigCache,
    ConfigSource,
    HeldConfig,
    RefreshPlan,
    obtain_config,
)
from stashd.config.errors import ConfigError
from stashd.db import create_engine, session_factory
from stashd.drivers.base import StorageDriver
from stashd.drivers.factory import build_drivers, check_pool_size
from stashd.engines.rsync import RsyncEngine
from stashd.identity.base import Identity
from stashd.identity.current import CurrentUserIdentity
from stashd.identity.sudo import SudoIdentity
from stashd.obs.logging import configure_logging
from stashd.tasks.runner import TaskRunner
from stashd.tasks.threaded import ThreadTaskRunner

CONFIG_ERROR = 2
CACHED_CONFIG = "cluster.yaml"

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


@dataclass
class Parts:
    """Everything a role needs, assembled before the server starts."""

    held: HeldConfig
    sessions: async_sessionmaker[AsyncSession] | None = None
    auth: AuthProvider | None = None
    peer_auth: TokenAuthProvider | None = None
    drivers: dict[str, StorageDriver] | None = None
    runner: TaskRunner | None = None
    store: FilesetStore | None = None
    dispatcher: TransferDispatcher | None = None
    refresh: RefreshPlan | None = None
    reload_from: Path | None = None
    registrar: HttpRegistrar | None = None
    announcement: Announcement | None = None


def _identity(kind: IdentityKind) -> Identity:
    """Sudo for now; development runs as its own user."""
    return SudoIdentity() if kind is IdentityKind.SUDO else CurrentUserIdentity()


def _peer_token(config: BootstrapConfig) -> TokenAuthProvider | None:
    if config.peer_token_file is None:
        return None
    return TokenAuthProvider.from_file(config.peer_token_file)


def _config_source(config: BootstrapConfig) -> ConfigSource:
    """Where a storage daemon gets the cluster config. A seam for tests."""
    if config.controller is None:  # pragma: no cover - the config model requires it
        raise ConfigError(Path("<bootstrap>"), ["a storage daemon needs a controller url"])
    token = config.controller.token_file.read_text().strip()
    return HttpConfigSource(config.controller.url, token)


def _event_reporter(config: BootstrapConfig) -> EventReporter:
    if config.controller is None:  # pragma: no cover - the config model requires it
        return NullEventReporter()
    return HttpEventReporter(
        config.controller.url,
        config.controller.token_file.read_text().strip(),
        config.node.daemon_id,
    )


def _controller_parts(config: BootstrapConfig, document: ConfigDocument) -> Parts:
    parts = Parts(
        held=HeldConfig(document=document, degraded=False),
        peer_auth=_peer_token(config),
        reload_from=config.cluster_config,
    )
    if config.database is not None:
        parts.sessions = session_factory(create_engine(config.database.url))
    parts.auth = MungeAuthProvider(document.config.auth.munge_socket, SystemIdentityLookup())
    if config.peer_token_file is not None:
        token = config.peer_token_file.read_text().strip()
        client = httpx2.AsyncClient()
        parts.store = HttpFilesetStore(document.config, token, client)
        parts.dispatcher = HttpTransferDispatcher(document.config, token, client)
    return parts


def _daemon_parts(config: BootstrapConfig) -> Parts:
    cache = ConfigCache(config.cache_dir / CACHED_CONFIG)
    source = _config_source(config)
    held = obtain_config(source, cache)
    cluster = held.document.config
    check_pool_size(cluster, config.worker_pool_size)
    identity = _identity(config.identity)
    drivers = build_drivers(
        cluster,
        config.node.storages,
        identity,
        daemon_id=config.node.daemon_id,
        config_path=cache.path,
    )
    return Parts(
        held=held,
        peer_auth=_peer_token(config),
        refresh=RefreshPlan(
            source=source, cache=cache, held=held, interval=config.config_refresh_interval
        ),
        drivers=drivers,
        registrar=HttpRegistrar(
            config.controller.url, config.controller.token_file.read_text().strip()
        )
        if config.controller is not None
        else None,
        announcement=Announcement(
            daemon_id=config.node.daemon_id,
            storages=list(config.node.storages),
            version=__version__,
        ),
        runner=ThreadTaskRunner(
            drivers=drivers,
            engine=RsyncEngine(identity),
            reporter=_event_reporter(config),
            pool_size=config.worker_pool_size,
        ),
    )


def _assemble(config: BootstrapConfig) -> Parts:
    if config.is_controller:
        if config.cluster_config is None:  # pragma: no cover - the config model requires it
            raise ConfigError(Path("<bootstrap>"), ["a controller needs a cluster_config path"])
        return _controller_parts(config, load_cluster_document(config.cluster_config))
    return _daemon_parts(config)


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
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    configure_logging(bootstrap.logging.level, bootstrap.logging.format)
    try:
        parts = _assemble(bootstrap)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(CONFIG_ERROR) from None
    _serve(
        create_app(
            bootstrap,
            parts.held.document.config,
            auth=parts.auth,
            sessions=parts.sessions,
            peer_auth=parts.peer_auth,
            drivers=parts.drivers,
            runner=parts.runner,
            store=parts.store,
            dispatcher=parts.dispatcher,
            document=parts.held.document,
            degraded=parts.held.degraded,
            refresh=parts.refresh,
            reload_from=parts.reload_from,
            registrar=parts.registrar,
            announcement=parts.announcement,
        ),
        bootstrap.server,
    )
