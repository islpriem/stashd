"""Filesets: reading them, creating one, releasing one.

Every fileset is readable by everyone; only its owner or an admin may
change it. Admission runs under an advisory lock on the user, so two requests that each
fit but together do not can never both be admitted.
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.filesets import FilesetStore
from stashd.config.cluster import ClusterConfig, StorageRole
from stashd.domain.allocation import (
    AllocationRequest,
    AllocationState,
    Conflict,
    admit,
    check_resize,
)
from stashd.domain.clock import Clock
from stashd.domain.errors import ErrorCode, NotFound, StashError
from stashd.domain.filesets import (
    FilesetKind,
    FilesetState,
    holds_allocation,
    next_fileset_state,
)
from stashd.domain.identity import Principal
from stashd.domain.references import validate_fileset_name
from stashd.domain.storage import FilesetLocation, Owner
from stashd.models import Fileset
from stashd.services import allocations, audit


async def list_filesets(
    session: AsyncSession,
    *,
    storage: str | None = None,
    user: str | None = None,
    name: str | None = None,
    kind: FilesetKind | None = None,
    state: FilesetState | None = None,
) -> Sequence[Fileset]:
    # A reference names the live fileset, so live rows come first and history follows,
    # newest first. Clients rely on this order to resolve STORAGE:name.
    query = sa.select(Fileset).order_by(
        Fileset.storage_id,
        Fileset.owner_user,
        Fileset.name,
        (Fileset.state == FilesetState.RELEASED),
        Fileset.id.desc(),
    )
    if storage is not None:
        query = query.where(Fileset.storage_id == storage)
    if user is not None:
        query = query.where(Fileset.owner_user == user)
    if name is not None:
        query = query.where(Fileset.name == name)
    if kind is not None:
        query = query.where(Fileset.kind == kind)
    if state is not None:
        query = query.where(Fileset.state == state)
    return list(await session.scalars(query))


async def get_fileset(session: AsyncSession, fileset_id: int) -> Fileset:
    fileset = await session.get(Fileset, fileset_id)
    if fileset is None:
        raise NotFound(f"no fileset with id {fileset_id}", fileset_id=fileset_id)
    return fileset


class Forbidden(StashError):
    code = ErrorCode.FORBIDDEN


class FilesetExists(StashError):
    code = ErrorCode.FILESET_EXISTS


class NotACacheStorage(StashError):
    code = ErrorCode.NOT_A_CACHE_STORAGE


async def lock_user(session: AsyncSession, user: str) -> None:
    """Serialise admission for one user across the whole cluster.

    Held until the transaction ends, which is the moment the reservation is visible to
    everyone else.
    """
    digest = hashlib.blake2b(user.encode(), digest_size=8).digest()
    key = int.from_bytes(digest, "big", signed=True)
    await session.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def admission_state(
    session: AsyncSession, cluster: ClusterConfig, user: str, storage_id: str
) -> AllocationState:
    usage = await allocations.usage_per_storage(session, user)
    limits = await allocations.limits_of(session, user)
    storage = cluster.storage(storage_id)
    cache_ids = {cache.id for cache in cluster.cache_storages()}
    storage_allocated = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Fileset.allocated_bytes), 0)).where(
            Fileset.storage_id == storage_id, Fileset.state != FilesetState.RELEASED
        )
    )
    fileset_count = await session.scalar(
        sa.select(sa.func.count()).where(
            Fileset.owner_user == user, Fileset.state != FilesetState.RELEASED
        )
    )
    offenders = await session.scalars(
        sa.select(Fileset.name).where(
            Fileset.owner_user == user,
            Fileset.over_allocation.is_(True),
            Fileset.state != FilesetState.RELEASED,
        )
    )
    return AllocationState(
        user_allocated_on_storage=usage.get(
            storage_id, allocations.Usage(0, 0)
        ).allocated_bytes,
        user_limit_on_storage=allocations.storage_limit(cluster, storage_id, limits),
        user_allocated_on_caches=sum(
            value.allocated_bytes for key, value in usage.items() if key in cache_ids
        ),
        user_total_limit=allocations.total_limit(cluster, limits),
        storage_allocated=int(storage_allocated or 0),
        storage_capacity_bytes=storage.capacity_bytes or 0,
        fill_limit=storage.fill_limit,
        user_fileset_count=int(fileset_count or 0),
        max_filesets_per_user=cluster.limits.max_filesets_per_user,
        over_allocation_filesets=tuple(offenders),
        storage_drained=False,
    )


def cache_storage(cluster: ClusterConfig, storage_id: str) -> object:
    try:
        storage = cluster.storage(storage_id)
    except KeyError:
        raise NotFound(f"no storage named {storage_id}", storage=storage_id) from None
    if not storage.has_role(StorageRole.CACHE):
        raise NotACacheStorage(
            f"{storage_id} holds no filesets: it has the roles {', '.join(storage.roles)}",
            storage=storage_id,
        )
    return storage


async def live_fileset(
    session: AsyncSession, owner_user: str, storage_id: str, name: str
) -> Fileset | None:
    query = sa.select(Fileset).where(
        Fileset.owner_user == owner_user,
        Fileset.storage_id == storage_id,
        Fileset.name == name,
        Fileset.state != FilesetState.RELEASED,
    )
    return (await session.scalars(query)).one_or_none()


async def create_output_fileset(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    store: FilesetStore,
    clock: Clock,
    actor: Principal,
    owner: Owner,
    storage_id: str,
    name: str,
    size_bytes: int,
) -> Fileset:
    """Reserve first, then create the directory, then READY."""
    cache_storage(cluster, storage_id)
    validate_fileset_name(name)

    await lock_user(session, owner.user)
    try:
        if await live_fileset(session, owner.user, storage_id, name) is not None:
            raise FilesetExists(
                f"{owner.user} already has a fileset {name} on {storage_id}",
                storage=storage_id,
                name=name,
            )
        admit(
            AllocationRequest(
                user=owner.user, storage_id=storage_id, requested_bytes=size_bytes
            ),
            await admission_state(session, cluster, owner.user, storage_id),
        )
    except StashError as refusal:
        audit.record(
            session,
            clock,
            actor=actor,
            subject_user=owner.user,
            object_type="fileset",
            object_id=f"{storage_id}:{name}",
            action="create",
            result=str(refusal.code),
            detail=refusal.details,
        )
        await session.commit()
        raise

    fileset = Fileset(
        name=name,
        owner_user=owner.user,
        owner_uid=owner.uid,
        owner_gid=owner.gid,
        storage_id=storage_id,
        kind=FilesetKind.OUTPUT,
        state=FilesetState.CREATING,
        path="",
        allocated_bytes=size_bytes,
        created_at=clock.now(),
    )
    session.add(fileset)
    await session.commit()

    try:
        location = await store.create(storage_id, owner, name, size_bytes)
    except Exception:
        # Nothing was created, so nothing may stay reserved.
        await session.delete(fileset)
        await session.commit()
        raise

    fileset.path = location.path
    fileset.state = next_fileset_state(fileset.state, FilesetState.READY)
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=owner.user,
        object_type="fileset",
        object_id=str(fileset.id),
        action="create",
        detail={"storage": storage_id, "name": name, "allocated_bytes": size_bytes},
    )
    await session.commit()
    return fileset


def location_of(fileset: Fileset) -> FilesetLocation:
    return FilesetLocation(
        storage_id=fileset.storage_id,
        name=fileset.name,
        owner=Owner(user=fileset.owner_user, uid=fileset.owner_uid, gid=fileset.owner_gid),
        path=fileset.path,
    )


async def resize_fileset(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    store: FilesetStore,
    clock: Clock,
    actor: Principal,
    is_admin: bool,
    fileset_id: int,
    size_bytes: int,
    force: bool,
) -> Fileset:
    """Growing re-runs admission; shrinking below usage needs an admin."""
    fileset = await session.get(Fileset, fileset_id)
    if fileset is None:
        raise NotFound(f"no fileset with id {fileset_id}", fileset_id=fileset_id)
    if fileset.owner_user != actor.username and not is_admin:
        raise Forbidden(
            f"{fileset.name} on {fileset.storage_id} belongs to {fileset.owner_user}",
            owner=fileset.owner_user,
        )
    if force and not is_admin:
        raise Forbidden("only an admin may force a resize below what is used")
    if not holds_allocation(fileset.state):
        raise Conflict(
            f"{fileset.storage_id}:{fileset.name} is {fileset.state}", state=str(fileset.state)
        )

    await lock_user(session, fileset.owner_user)
    try:
        delta = check_resize(
            current=fileset.allocated_bytes,
            used=fileset.used_bytes,
            new=size_bytes,
            force=force,
        )
        if delta > 0:
            admit(
                AllocationRequest(
                    user=fileset.owner_user,
                    storage_id=fileset.storage_id,
                    requested_bytes=delta,
                ),
                await admission_state(session, cluster, fileset.owner_user, fileset.storage_id),
            )
    except StashError as refusal:
        audit.record(
            session,
            clock,
            actor=actor,
            subject_user=fileset.owner_user,
            object_type="fileset",
            object_id=str(fileset.id),
            action="resize",
            result=str(refusal.code),
            detail=refusal.details,
        )
        await session.commit()
        raise

    fileset.allocated_bytes = size_bytes
    await store.set_quota(location_of(fileset), size_bytes)
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=fileset.owner_user,
        object_type="fileset",
        object_id=str(fileset.id),
        action="resize",
        detail={"allocated_bytes": size_bytes, "forced": force},
    )
    await session.commit()
    return fileset
