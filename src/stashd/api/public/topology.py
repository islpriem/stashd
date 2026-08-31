"""Who am I, and what does the cluster look like."""

from fastapi import APIRouter

from stashd import __version__
from stashd.api.deps import Caller, Cluster, caller_is_admin
from stashd.schemas.topology import Location, Locations, Storage, Storages, WhoAmI

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
async def storages(config: Cluster) -> Storages:
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
            )
            for storage in config.storages
        ]
    )
