"""Assembling the FastAPI application for the role this process runs."""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stashd import __version__
from stashd.api.deps import peer, principal
from stashd.api.errors import install_error_handlers
from stashd.api.health import health_router
from stashd.api.internal import config as internal_config
from stashd.api.internal import events as internal_events
from stashd.api.internal import filesets as internal_filesets
from stashd.api.internal import tasks as internal_tasks
from stashd.api.middleware import RequestContextMiddleware
from stashd.api.public import allocations, filesets, topology, transfers
from stashd.auth.owners import OwnerLookup, SystemOwnerLookup
from stashd.auth.provider import AuthProvider
from stashd.auth.token import TokenAuthProvider
from stashd.clients.filesets import FilesetStore
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig, ConfigDocument
from stashd.config.distribution import RefreshPlan, refresh_config
from stashd.config.errors import ConfigError
from stashd.config.reload import reload_document
from stashd.domain.clock import Clock, SystemClock
from stashd.drivers.base import StorageDriver
from stashd.schemas.errors import ErrorEnvelope
from stashd.tasks.runner import TaskRunner

logger = structlog.get_logger()

API_PREFIX = "/api/v1"
INTERNAL_PREFIX = "/internal/v1"


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorEnvelope, "description": description}
    for code, description in (
        (400, "The request could not be understood."),
        (401, "No valid credential."),
        (403, "Not permitted."),
        (404, "No such object."),
        (409, "Refused by a rule: a limit, a state, or a conflict."),
        (500, "Unexpected failure."),
        (503, "A storage or daemon is unavailable."),
    )
}


def public_router() -> APIRouter:
    """Every public route needs a verified credential."""
    router = APIRouter(
        prefix=API_PREFIX, dependencies=[Depends(principal)], responses=ERROR_RESPONSES
    )
    router.include_router(topology.router)
    router.include_router(filesets.router)
    router.include_router(transfers.router)
    router.include_router(allocations.router)
    return router


def internal_router() -> APIRouter:
    """Both roles serve this; a peer token is the only credential it accepts."""
    router = APIRouter(
        prefix=INTERNAL_PREFIX, dependencies=[Depends(peer)], responses=ERROR_RESPONSES
    )
    router.include_router(internal_filesets.router)
    router.include_router(internal_config.router)
    router.include_router(internal_tasks.router)
    router.include_router(internal_events.router)
    return router


def apply_document(app: FastAPI, document: ConfigDocument, *, degraded: bool) -> None:
    """Swap in a config every later request will see."""
    app.state.document = document
    app.state.cluster = document.config
    app.state.config_degraded = degraded


def reload_cluster_config(app: FastAPI) -> bool:
    """Re-read what the controller authored, keeping the running config on any doubt."""
    path: Path | None = app.state.reload_from
    document: ConfigDocument | None = app.state.document
    if path is None or document is None:
        return False
    try:
        reloaded = reload_document(path, document)
    except ConfigError as error:
        logger.error("config.reload_refused", path=str(path), problems=error.problems)
        return False
    if reloaded is None:
        return False
    apply_document(app, reloaded, degraded=False)
    logger.info("config.reloaded", revision=reloaded.revision)
    return True


async def refresh_cluster_config(app: FastAPI) -> bool:
    """One refresh against the controller, off the event loop."""
    plan: RefreshPlan | None = app.state.refresh
    if plan is None:
        return False
    changed = await asyncio.to_thread(refresh_config, plan.source, plan.cache, plan.held)
    apply_document(app, plan.held.document, degraded=plan.held.degraded)
    return changed


async def _refresh_loop(app: FastAPI, plan: RefreshPlan) -> None:  # pragma: no cover - a loop
    while True:
        await asyncio.sleep(plan.interval.total_seconds())
        await refresh_cluster_config(app)


@asynccontextmanager
async def background(app: FastAPI) -> AsyncIterator[None]:
    """Keep the cluster config current: refresh on a daemon, SIGHUP on the controller."""
    tasks: list[asyncio.Task[None]] = []
    plan: RefreshPlan | None = app.state.refresh
    if plan is not None:
        tasks.append(asyncio.create_task(_refresh_loop(app, plan)))
    if app.state.reload_from is not None:  # pragma: no cover - needs a real signal
        with suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGHUP, lambda: reload_cluster_config(app)
            )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


def create_app(
    bootstrap: BootstrapConfig,
    cluster: ClusterConfig | None = None,
    *,
    auth: AuthProvider | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    peer_auth: TokenAuthProvider | None = None,
    drivers: dict[str, StorageDriver] | None = None,
    runner: TaskRunner | None = None,
    store: FilesetStore | None = None,
    document: ConfigDocument | None = None,
    degraded: bool = False,
    refresh: RefreshPlan | None = None,
    reload_from: Path | None = None,
    owners: OwnerLookup | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app for one process. Only a controller serves ``/api/v1``."""
    app = FastAPI(title="stashd", version=__version__, lifespan=background)
    app.state.bootstrap = bootstrap
    app.state.cluster = cluster
    app.state.auth = auth
    app.state.sessions = sessions
    app.state.peer_auth = peer_auth
    app.state.drivers = drivers
    app.state.runner = runner
    app.state.store = store
    app.state.document = document
    app.state.config_degraded = degraded
    app.state.refresh = refresh
    app.state.reload_from = reload_from
    app.state.owners = owners or SystemOwnerLookup()
    app.state.clock = clock or SystemClock()
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router(bootstrap))
    if bootstrap.is_controller:
        app.include_router(public_router())
    app.include_router(internal_router())
    return app
