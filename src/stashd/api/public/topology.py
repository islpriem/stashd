"""Who am I, and what does the cluster look like."""

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from stashd import __version__
from stashd.api.deps import Caller, Cluster, Session, Ticking, caller_is_admin
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.identity import Principal
from stashd.drivers.factory import capabilities_for
from stashd.schemas.topology import (
    DrainState,
    Location,
    Locations,
    Storage,
    Storages,
    WhoAmI,
)
from stashd.services import drain
from stashd.services.daemons import known_daemons
from stashd.services.filesets import Forbidden

API_VERSION = "v1"

router = APIRouter()


@router.get("/whoami")
async def whoami(caller: Caller, config: Cluster) -> WhoAmI:
    return WhoAmI(
        uid=caller.uid,
        username=caller.username,
        groups=list(caller.groups),
        admin=caller_is_admin(caller, config),
        server_version=__version__,
        api_version=API_VERSION,
    )


@router.get("/locations")
async def locations(config: Cluster) -> Locations:
    return Locations(
        locations=[
            Location(id=location.id, name=location.name, enabled=location.enabled)
            for location in config.locations
        ]
    )


@router.get("/storages")
async def storages(config: Cluster, session: Session) -> Storages:
    seen = await known_daemons(session, [storage.daemon for storage in config.storages])
    drained = await drain.drained_storages(session)
    return Storages(
        storages=[
            Storage(
                id=storage.id,
                location=storage.location,
                roles=storage.roles,
                tier=storage.tier,
                driver=storage.driver,
                fileset_prefix=str(storage.fileset_prefix),
                capacity_bytes=storage.capacity_bytes,
                fill_limit=storage.fill_limit,
                default_user_allocation_limit_bytes=storage.default_user_allocation_limit_bytes,
                daemon=storage.daemon,
                drained=storage.id in drained,
                enabled=storage.enabled,
                quota_enforced=capabilities_for(storage.driver).native_quota,
                daemon_seen_at=seen[storage.daemon].last_seen_at
                if storage.daemon in seen
                else None,
                daemon_config_revision=seen[storage.daemon].config_revision
                if storage.daemon in seen
                else None,
            )
            for storage in config.storages
        ]
    )


@router.post("/storages/{storage_id}/drain")
async def drain_storage(
    caller: Caller, config: Cluster, session: Session, clock: Ticking, storage_id: str
) -> DrainState:
    """No new dispatches and no new filesets; what is running finishes."""
    return await _set_drain(caller, config, session, clock, storage_id, drained=True)


@router.post("/storages/{storage_id}/undrain")
async def undrain_storage(
    caller: Caller, config: Cluster, session: Session, clock: Ticking, storage_id: str
) -> DrainState:
    return await _set_drain(caller, config, session, clock, storage_id, drained=False)


async def _set_drain(
    caller: Principal,
    config: ClusterConfig,
    session: AsyncSession,
    clock: Clock,
    storage_id: str,
    *,
    drained: bool,
) -> DrainState:
    if not caller_is_admin(caller, config):
        raise Forbidden("only an admin may drain a storage", storage=storage_id)
    await drain.set_drained(
        session,
        cluster=config,
        clock=clock,
        actor=caller,
        storage_id=storage_id,
        drained=drained,
    )
    return DrainState(storage=storage_id, drained=drained)
