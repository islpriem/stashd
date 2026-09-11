"""Submitting a warm.

Everything is decided before anything is persisted: the source is probed on its own
daemon, the name is checked, and admission runs under the same per-user lock as a
creation. A dry run stops there and answers with the numbers.
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig, StorageRole
from stashd.domain.allocation import (
    AllocationRequest,
    admit,
    allocation_for_source,
)
from stashd.domain.clock import Clock
from stashd.domain.errors import ErrorCode, NotFound, StashError
from stashd.domain.fairshare import points_for_bytes
from stashd.domain.filesets import FilesetKind, FilesetState, next_fileset_state
from stashd.domain.identity import Principal
from stashd.domain.references import validate_fileset_name
from stashd.domain.routes import Route, estimate, throughput_for
from stashd.domain.storage import Owner
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer
from stashd.services import audit, fairshare
from stashd.services.filesets import (
    FilesetExists,
    admission_state,
    cache_storage,
    live_fileset,
    lock_user,
)


class NotASourceStorage(StashError):
    code = ErrorCode.NOT_A_SOURCE_STORAGE


class PathNotFound(StashError):
    code = ErrorCode.PATH_NOT_FOUND


class PermissionDenied(StashError):
    code = ErrorCode.PERMISSION_DENIED


class SourceMismatch(StashError):
    code = ErrorCode.SOURCE_MISMATCH


class Conflict(StashError):
    code = ErrorCode.CONFLICT


class TooManyQueued(StashError):
    code = ErrorCode.TOO_MANY_QUEUED


@dataclass(frozen=True, slots=True)
class Preflight:
    """What the submission decided, and what a dry run reports instead of acting."""

    source_reference: str
    target_reference: str
    path: str
    route: Route
    bytes_total: int
    file_count: int
    allocation_bytes: int
    refresh: bool
    queued_ahead_bytes: int


def source_storage(cluster: ClusterConfig, storage_id: str) -> object:
    try:
        storage = cluster.storage(storage_id)
    except KeyError:
        raise NotFound(f"no storage named {storage_id}", storage=storage_id) from None
    if not storage.has_role(StorageRole.SOURCE):
        raise NotASourceStorage(
            f"{storage_id} is not a source storage: its roles are {', '.join(storage.roles)}",
            storage=storage_id,
        )
    return storage


async def preflight(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    owner: Owner,
    source_storage_id: str,
    source_path: str,
    target_storage_id: str,
    name: str,
    size_bytes: int | None,
    refresh: bool,
) -> Preflight:
    source_storage(cluster, source_storage_id)
    cache_storage(cluster, target_storage_id)
    validate_fileset_name(name)

    probe = await dispatcher.probe(source_storage_id, owner, source_path)
    if not probe.exists:
        raise PathNotFound(
            f"{source_storage_id}:{source_path} does not exist",
            storage=source_storage_id,
            path=source_path,
        )
    if not probe.readable:
        raise PermissionDenied(
            f"{owner.user} cannot read {source_storage_id}:{source_path}",
            storage=source_storage_id,
            path=source_path,
        )

    existing = await live_fileset(session, owner.user, target_storage_id, name)
    reference = f"{source_storage_id}:{source_path}"
    if existing is not None:
        _check_refresh(existing, reference, refresh, target_storage_id, name)

    allocation = allocation_for_source(
        probe.bytes_total, headroom=cluster.scheduling.allocation_headroom, requested=size_bytes
    )
    route = Route(source_storage_id, target_storage_id)
    return Preflight(
        source_reference=reference,
        target_reference=f"{target_storage_id}:{name}",
        path=existing.path if existing is not None else "",
        route=route,
        bytes_total=probe.bytes_total,
        file_count=probe.file_count,
        allocation_bytes=allocation,
        refresh=existing is not None,
        queued_ahead_bytes=await _queued_ahead(session, route),
    )


def _check_refresh(
    existing: Fileset, source: str, refresh: bool, storage_id: str, name: str
) -> None:
    if existing.kind is not FilesetKind.CACHED:
        raise FilesetExists(
            f"{storage_id}:{name} is an output fileset; a warm would overwrite it",
            storage=storage_id,
            name=name,
        )
    current = f"{existing.source_storage_id}:{existing.source_path}"
    if current != source:
        raise SourceMismatch(
            f"{storage_id}:{name} was filled from {current}, not from {source}",
            storage=storage_id,
            name=name,
            source=current,
        )
    if not refresh:
        raise Conflict(
            f"{storage_id}:{name} already holds {source}; pass refresh to fill it again",
            storage=storage_id,
            name=name,
        )
    if existing.state is FilesetState.POPULATING:
        raise Conflict(f"{storage_id}:{name} is being filled already", storage=storage_id)


async def _queued_ahead(session: AsyncSession, route: Route) -> int:
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Transfer.bytes_total), 0)).where(
            Transfer.route == str(route), Transfer.state == TransferState.SUBMITTED
        )
    )
    return int(total or 0)


def eta(
    cluster: ClusterConfig,
    route: Route,
    bytes_total: int,
    queued_ahead_bytes: int = 0,
) -> tuple[int, int]:
    """When a transfer of this size on this route would start and how long it would take."""
    throughput = throughput_for(
        route,
        default=cluster.transfer.nominal_throughput.default,
        routes=cluster.transfer.nominal_throughput.routes,
    )
    start, duration = estimate(
        bytes_total=bytes_total, queued_ahead=queued_ahead_bytes, throughput=throughput
    )
    return int(start.total_seconds()), int(duration.total_seconds())


async def submit_warm(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    clock: Clock,
    actor: Principal,
    owner: Owner,
    plan: Preflight,
    source_storage_id: str,
    source_path: str,
    target_storage_id: str,
    name: str,
) -> Transfer:
    await lock_user(session, owner.user)
    try:
        await _check_queue_length(session, cluster, owner.user)
        existing = await live_fileset(session, owner.user, target_storage_id, name)
        already = existing.allocated_bytes if existing is not None else 0
        if plan.allocation_bytes > already:
            admit(
                AllocationRequest(
                    user=owner.user,
                    storage_id=target_storage_id,
                    requested_bytes=plan.allocation_bytes - already,
                ),
                await admission_state(session, cluster, owner.user, target_storage_id),
            )
    except StashError as refusal:
        audit.record(
            session,
            clock,
            actor=actor,
            subject_user=owner.user,
            object_type="transfer",
            object_id=plan.target_reference,
            action="warm",
            result=str(refusal.code),
            detail=refusal.details,
        )
        await session.commit()
        raise

    fileset = existing or Fileset(
        name=name,
        owner_user=owner.user,
        owner_uid=owner.uid,
        owner_gid=owner.gid,
        storage_id=target_storage_id,
        kind=FilesetKind.CACHED,
        state=FilesetState.CREATING,
        path="",
        allocated_bytes=plan.allocation_bytes,
        source_storage_id=source_storage_id,
        source_path=source_path,
        created_at=clock.now(),
    )
    fileset.allocated_bytes = max(fileset.allocated_bytes, plan.allocation_bytes)
    session.add(fileset)
    await session.flush()

    transfer = Transfer(
        kind=TransferKind.WARM,
        user=owner.user,
        fileset_id=fileset.id,
        peer_ref=plan.source_reference,
        state=TransferState.SUBMITTED,
        route=str(plan.route),
        bytes_total=plan.bytes_total,
        submitted_at=clock.now(),
    )
    session.add(transfer)
    fileset.warm_started_at = clock.now()
    await session.commit()

    await _prepare_destination(dispatcher=dispatcher, owner=owner, fileset=fileset)
    # Anti queue-stuffing: the estimate is charged now, and refunded if it never runs.
    await fairshare.charge(
        session,
        owner.user,
        points_for_bytes(
            TransferKind.WARM,
            plan.bytes_total,
            points_per_gib=cluster.scheduling.fairshare.points_per_gib,
        ),
        clock,
        cluster.scheduling.fairshare.half_life,
    )
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=owner.user,
        object_type="transfer",
        object_id=str(transfer.id),
        action="warm",
        detail={"source": plan.source_reference, "target": plan.target_reference},
    )
    await session.commit()
    return transfer


async def _check_queue_length(session: AsyncSession, cluster: ClusterConfig, user: str) -> None:
    queued = await session.scalar(
        sa.select(sa.func.count()).where(
            Transfer.user == user,
            Transfer.state.in_([TransferState.SUBMITTED, TransferState.ASSIGNED]),
        )
    )
    limit = cluster.limits.queued_transfers_per_user
    if int(queued or 0) >= limit:
        raise TooManyQueued(
            f"you already have {queued} transfers queued; the limit is {limit}",
            queued=int(queued or 0),
            limit=limit,
        )


async def _prepare_destination(
    *,
    dispatcher: TransferDispatcher,
    owner: Owner,
    fileset: Fileset,
) -> None:
    """The directory exists as soon as the warm is accepted, so its path can be returned.

    Moving the data is the scheduler's decision, not this request's.
    """
    endpoint = await dispatcher.prepare(
        fileset.storage_id, owner, fileset.name, fileset.allocated_bytes
    )
    fileset.path = endpoint.path
    if fileset.state is FilesetState.CREATING:
        fileset.state = next_fileset_state(fileset.state, FilesetState.READY)
    fileset.state = next_fileset_state(fileset.state, FilesetState.POPULATING)
