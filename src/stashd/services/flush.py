"""Submitting a flush: an output fileset written back to a source storage.

A flush is a scheduled transfer like a warm, subject to the same queue limits and
fair share. It reserves nothing: the target is a source storage,
which STASH does not allocate. It never uses ``--delete``.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import InvalidPath, NotFound, StashError
from stashd.domain.fairshare import points_for_bytes
from stashd.domain.filesets import FilesetState, next_fileset_state
from stashd.domain.identity import Principal
from stashd.domain.routes import Route
from stashd.domain.storage import Owner
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer
from stashd.services import audit, fairshare
from stashd.services.filesets import Forbidden, live_fileset
from stashd.services.warm import (
    PathNotFound,
    PermissionDenied,
    _check_queue_length,
    source_storage,
)


@dataclass(frozen=True, slots=True)
class Plan:
    """What the flush decided, and what a dry run answers instead of acting."""

    fileset: Fileset
    source_reference: str
    target_reference: str
    path: str
    route: Route
    bytes_total: int
    file_count: int


def relative_path(fileset: Fileset) -> str:
    """How the owning storage names the fileset: filesets live at /<user>/<name>."""
    return f"/{fileset.owner_user}/{fileset.name}"


async def preflight(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    dispatcher: TransferDispatcher,
    actor: Principal,
    owner: Owner,
    is_admin: bool,
    fileset_storage_id: str,
    name: str,
    target_storage_id: str,
    target_path: str,
) -> Plan:
    source_storage(cluster, target_storage_id)
    fileset = await live_fileset(session, owner.user, fileset_storage_id, name)
    if fileset is None:
        raise NotFound(
            f"{owner.user} has no fileset {name} on {fileset_storage_id}",
            storage=fileset_storage_id,
            name=name,
        )
    if fileset.owner_user != actor.username and not is_admin:
        raise Forbidden(
            f"{name} on {fileset_storage_id} belongs to {fileset.owner_user}",
            owner=fileset.owner_user,
        )

    target = await dispatcher.probe(target_storage_id, owner, target_path)
    reference = f"{target_storage_id}:{target_path}"
    if not target.exists:
        # A flush merges into what is there and never creates it:
        # STASH writes nothing outside a fileset or an existing writable target.
        raise PathNotFound(
            f"{reference} does not exist", storage=target_storage_id, path=target_path
        )
    if not target.is_dir:
        raise InvalidPath(
            f"{reference} is a file; a flush target is a directory",
            storage=target_storage_id,
            path=target_path,
        )
    if not target.writable:
        raise PermissionDenied(
            f"{owner.user} cannot write to {reference}",
            storage=target_storage_id,
            path=target_path,
        )

    measured = await dispatcher.probe(fileset_storage_id, owner, relative_path(fileset))
    return Plan(
        fileset=fileset,
        source_reference=f"{fileset_storage_id}:{name}",
        target_reference=reference,
        path=target.path,
        route=Route(fileset_storage_id, target_storage_id),
        bytes_total=measured.bytes_total or fileset.used_bytes,
        file_count=measured.file_count or (fileset.file_count or 0),
    )


async def submit_flush(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    clock: Clock,
    actor: Principal,
    owner: Owner,
    plan: Plan,
    keep: bool,
) -> Transfer:
    try:
        await _check_queue_length(session, cluster, owner.user)
    except StashError as refusal:
        audit.record(
            session,
            clock,
            actor=actor,
            subject_user=owner.user,
            object_type="transfer",
            object_id=plan.source_reference,
            action="flush",
            result=str(refusal.code),
            detail=refusal.details,
        )
        await session.commit()
        raise

    fileset = plan.fileset
    transfer = Transfer(
        kind=TransferKind.FLUSH,
        user=owner.user,
        fileset_id=fileset.id,
        peer_ref=plan.target_reference,
        state=TransferState.SUBMITTED,
        route=str(plan.route),
        bytes_total=plan.bytes_total,
        release_after=not keep,
        submitted_at=clock.now(),
    )
    session.add(transfer)
    fileset.state = next_fileset_state(fileset.state, FilesetState.FLUSHING)
    await session.commit()

    await fairshare.charge(
        session,
        owner.user,
        points_for_bytes(
            TransferKind.FLUSH,
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
        action="flush",
        detail={
            "source": plan.source_reference,
            "target": plan.target_reference,
            "keep": keep,
        },
    )
    await session.commit()
    return transfer
