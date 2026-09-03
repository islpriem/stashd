"""Liveness and readiness, served by both roles."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from stashd import __version__
from stashd.config.bootstrap import BootstrapConfig


class Health(BaseModel):
    status: str
    role: str
    daemon_id: str
    version: str


class Readiness(BaseModel):
    ready: bool
    degraded: bool
    config_revision: int | None
    checks: dict[str, str]


def health_router(bootstrap: BootstrapConfig) -> APIRouter:
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
    async def readyz(request: Request, response: Response) -> Readiness:
        """Ready means it can work. Degraded means it is working on a cached config."""
        current = request.app.state.cluster
        if current is None:
            response.status_code = 503
            return Readiness(
                ready=False,
                degraded=True,
                config_revision=None,
                checks={"cluster_config": "not fetched"},
            )
        degraded = bool(request.app.state.config_degraded)
        return Readiness(
            ready=True,
            degraded=degraded,
            config_revision=current.revision,
            checks={"cluster_config": "from cache: the controller was unreachable"}
            if degraded
            else {},
        )

    return router
