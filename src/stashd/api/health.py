"""Liveness and readiness, served by both roles."""

from fastapi import APIRouter, Response
from pydantic import BaseModel

from stashd import __version__
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig


class Health(BaseModel):
    status: str
    role: str
    daemon_id: str
    version: str


class Readiness(BaseModel):
    ready: bool
    config_revision: int | None
    checks: dict[str, str]


def health_router(bootstrap: BootstrapConfig, cluster: ClusterConfig | None) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> Health:
        return Health(
            status="ok",
            role=bootstrap.node.role,
            daemon_id=bootstrap.node.daemon_id,
            version=__version__,
        )

    @router.get("/readyz")
    async def readyz(response: Response) -> Readiness:
        checks = {} if cluster is not None else {"cluster_config": "not fetched"}
        if checks:
            response.status_code = 503
        return Readiness(
            ready=not checks,
            config_revision=cluster.revision if cluster else None,
            checks=checks,
        )

    return router
