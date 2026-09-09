"""Liveness and readiness, served by both roles."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from stashd import __version__
from stashd.config.bootstrap import BootstrapConfig
from stashd.obs.metrics import CONTENT_TYPE, render


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

    @router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """What the cluster looks like right now, read from the database."""
        sessions = request.app.state.sessions
        cluster = request.app.state.cluster
        if sessions is None or cluster is None:
            return Response("", media_type=CONTENT_TYPE)
        async with sessions() as session:
            body = await render(
                session,
                cluster=cluster,
                clock=request.app.state.clock,
                unreachable_after=cluster.timeouts.daemon_unreachable,
            )
        return Response(body, media_type=CONTENT_TYPE)

    return router
