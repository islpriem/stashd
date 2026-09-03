"""A daemon announcing itself to the controller."""

from fastapi import APIRouter

from stashd.api.deps import Cluster, Session, Ticking
from stashd.schemas.internal import Registered, Registration
from stashd.services.daemons import Registration as Announcement
from stashd.services.daemons import register

router = APIRouter()


@router.post("/register")
async def register_daemon(
    session: Session, cluster: Cluster, clock: Ticking, body: Registration
) -> Registered:
    daemon = await register(
        session,
        cluster,
        clock,
        Announcement(
            daemon_id=body.daemon_id,
            storages=list(body.storages),
            config_revision=body.config_revision,
            version=body.version,
        ),
    )
    return Registered(
        daemon_id=daemon.id,
        config_revision=cluster.revision,
        refresh_needed=daemon.config_revision < cluster.revision,
    )
