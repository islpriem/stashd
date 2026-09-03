"""Who am I, and what does the cluster look like."""

from fastapi import APIRouter

from stashd import __version__
from stashd.api.deps import Caller, Cluster, Session, caller_is_admin
from stashd.drivers.factory import capabilities_for
from stashd.schemas.topology import Location, Locations, Storage, Storages, WhoAmI
from stashd.services.daemons import known_daemons

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
                drained=False,
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
