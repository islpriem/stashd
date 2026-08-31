"""Allocation and limit reads."""

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Query

from stashd.api.deps import Caller, Cluster, Session
from stashd.models import UserLimit
from stashd.schemas.allocations import (
    Allocations,
    AllocationTotals,
    Limit,
    Limits,
    StorageAllocation,
)
from stashd.services import allocations as service

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
