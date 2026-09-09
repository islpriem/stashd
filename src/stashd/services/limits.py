"""Per-user allocation limits, as an admin changes them.

Lowering a limit below what a user already holds is allowed: it stops the next
allocation, it never touches data. Clearing one falls back to the configured default.
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import NotFound
from stashd.domain.identity import Principal
from stashd.models import UserLimit
from stashd.services import audit


def _check_storage(cluster: ClusterConfig, storage_id: str | None) -> None:
    if storage_id is None:
        return
    try:
        cluster.storage(storage_id)
    except KeyError:
        raise NotFound(f"no storage named {storage_id}", storage=storage_id) from None


async def _find(session: AsyncSession, user: str, storage_id: str | None) -> UserLimit | None:
    matches_storage = (
        UserLimit.storage_id.is_(None)
        if storage_id is None
        else UserLimit.storage_id == storage_id
    )
    found: UserLimit | None = await session.scalar(
        sa.select(UserLimit).where(UserLimit.user == user, matches_storage)
    )
    return found


async def set_limit(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    clock: Clock,
    actor: Principal,
    user: str,
    storage_id: str | None,
    allocation_limit_bytes: int,
) -> UserLimit:
    _check_storage(cluster, storage_id)
    limit = await _find(session, user, storage_id)
    if limit is None:
        limit = UserLimit(
            user=user, storage_id=storage_id, allocation_limit_bytes=allocation_limit_bytes
        )
        session.add(limit)
    else:
        limit.allocation_limit_bytes = allocation_limit_bytes
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=user,
        object_type="limit",
        object_id=storage_id or "cluster",
        action="set_limit",
        detail={"allocation_limit_bytes": allocation_limit_bytes},
    )
    await session.commit()
    return limit


async def clear_limit(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    clock: Clock,
    actor: Principal,
    user: str,
    storage_id: str | None,
) -> None:
    _check_storage(cluster, storage_id)
    limit = await _find(session, user, storage_id)
    if limit is None:
        raise NotFound(
            f"{user} has no limit set for {storage_id or 'all caches'}",
            user=user,
            storage=storage_id,
        )
    await session.delete(limit)
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=user,
        object_type="limit",
        object_id=storage_id or "cluster",
        action="clear_limit",
    )
    await session.commit()
