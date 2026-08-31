"""Assembling the FastAPI application for the role this process runs."""

from fastapi import FastAPI

from stashd import __version__
from stashd.api.health import health_router
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig


def create_app(bootstrap: BootstrapConfig, cluster: ClusterConfig | None = None) -> FastAPI:
    """Build the app for one process. Only a controller serves ``/api/v1``."""
    app = FastAPI(title="stashd", version=__version__)
    app.state.bootstrap = bootstrap
    app.state.cluster = cluster
    app.include_router(health_router(bootstrap, cluster))
    return app
