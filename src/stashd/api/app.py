"""Assembling the FastAPI application for the role this process runs."""

from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stashd import __version__
from stashd.api.deps import principal
from stashd.api.errors import install_error_handlers
from stashd.api.health import health_router
from stashd.api.middleware import RequestContextMiddleware
from stashd.api.public import allocations, filesets, topology, transfers
from stashd.auth.provider import AuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig
from stashd.schemas.errors import ErrorEnvelope

API_PREFIX = "/api/v1"


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


def create_app(
    bootstrap: BootstrapConfig,
    cluster: ClusterConfig | None = None,
    *,
    auth: AuthProvider | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    """Build the app for one process. Only a controller serves ``/api/v1``."""
    app = FastAPI(title="stashd", version=__version__)
    app.state.bootstrap = bootstrap
    app.state.cluster = cluster
    app.state.auth = auth
    app.state.sessions = sessions
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router(bootstrap, cluster))
    if bootstrap.is_controller:
        app.include_router(public_router())
    return app
