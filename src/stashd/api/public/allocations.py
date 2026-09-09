"""Allocation and limit reads."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import Field

from stashd.api.deps import Caller, Cluster, Session, Ticking, caller_is_admin, owner_lookup
from stashd.auth.owners import owner_for
from stashd.domain.identity import Principal
from stashd.models import UserLimit
from stashd.schemas.allocations import (
    Allocations,
    AllocationTotals,
    Limit,
    Limits,
    StorageAllocation,
)
from stashd.schemas.base import Wire
from stashd.services import allocations as service
from stashd.services import limits as limits_service
from stashd.services.filesets import Forbidden

router = APIRouter()


@router.get("/allocations")
async def allocations(
    caller: Caller,
    config: Cluster,
    session: Session,
    user: Annotated[str | None, Query()] = None,
) -> Allocations:
    subject = user or caller.username
    usage = await service.usage_per_storage(session, subject)
    limits = await service.limits_of(session, subject)

    per_storage = []
    for storage in config.cache_storages():
        stored = usage.get(storage.id, service.Usage(0, 0))
        limit = service.storage_limit(config, storage.id, limits)
        per_storage.append(
            StorageAllocation(
                storage_id=storage.id,
                limit_bytes=limit,
                allocated_bytes=stored.allocated_bytes,
                used_bytes=stored.used_bytes,
                free_bytes=max(limit - stored.allocated_bytes, 0),
            )
        )

    cache_ids = {storage.id for storage in config.cache_storages()}
    allocated = sum(value.allocated_bytes for key, value in usage.items() if key in cache_ids)
    used = sum(value.used_bytes for key, value in usage.items() if key in cache_ids)
    limit = service.total_limit(config, limits)
    return Allocations(
        user=subject,
        total=AllocationTotals(
            limit_bytes=limit,
            allocated_bytes=allocated,
            used_bytes=used,
            free_bytes=max(limit - allocated, 0),
        ),
        storages=per_storage,
    )


@router.get("/limits")
async def limits(session: Session, user: Annotated[str | None, Query()] = None) -> Limits:
    query = sa.select(UserLimit).order_by(UserLimit.user, UserLimit.storage_id)
    if user is not None:
        query = query.where(UserLimit.user == user)
    rows = await session.scalars(query)
    return Limits(
        limits=[
            Limit(
                user=row.user,
                storage_id=row.storage_id,
                allocation_limit_bytes=row.allocation_limit_bytes,
            )
            for row in rows
        ]
    )


class SetLimit(Wire):
    allocation_limit_bytes: int = Field(ge=0)


def _admin_for(request: Request, caller: Principal, config: Cluster, user: str) -> None:
    """Writing a limit is admin-only, and the user has to exist."""
    if not caller_is_admin(caller, config):
        raise Forbidden("only an admin may change a limit", user=user)
    owner_for(caller, user, owner_lookup(request))


@router.put("/limits/{user}")
async def set_total_limit(
    request: Request,
    caller: Caller,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
    body: SetLimit,
) -> Limit:
    """The cluster-wide limit: what the user may hold across all caches."""
    return await _set(request, caller, config, session, clock, user, None, body)


@router.put("/limits/{user}/{storage_id}")
async def set_storage_limit(
    request: Request,
    caller: Caller,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
    storage_id: str,
    body: SetLimit,
) -> Limit:
    return await _set(request, caller, config, session, clock, user, storage_id, body)


@router.delete("/limits/{user}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_total_limit(
    request: Request,
    caller: Caller,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
) -> Response:
    return await _clear(request, caller, config, session, clock, user, None)


@router.delete("/limits/{user}/{storage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_storage_limit(
    request: Request,
    caller: Caller,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
    storage_id: str,
) -> Response:
    return await _clear(request, caller, config, session, clock, user, storage_id)


async def _set(
    request: Request,
    caller: Principal,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
    storage_id: str | None,
    body: SetLimit,
) -> Limit:
    _admin_for(request, caller, config, user)
    row = await limits_service.set_limit(
        session,
        cluster=config,
        clock=clock,
        actor=caller,
        user=user,
        storage_id=storage_id,
        allocation_limit_bytes=body.allocation_limit_bytes,
    )
    return Limit(
        user=row.user,
        storage_id=row.storage_id,
        allocation_limit_bytes=row.allocation_limit_bytes,
    )


async def _clear(
    request: Request,
    caller: Principal,
    config: Cluster,
    session: Session,
    clock: Ticking,
    user: str,
    storage_id: str | None,
) -> Response:
    _admin_for(request, caller, config, user)
    await limits_service.clear_limit(
        session, cluster=config, clock=clock, actor=caller, user=user, storage_id=storage_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
