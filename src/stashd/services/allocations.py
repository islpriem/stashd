"""What a user has reserved, against the limits that apply to them."""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.filesets import FilesetState
from stashd.models import Fileset, UserLimit


@dataclass(frozen=True, slots=True)
class Usage:
    allocated_bytes: int
    used_bytes: int


async def usage_per_storage(session: AsyncSession, user: str) -> dict[str, Usage]:
    """Live filesets only: a released one holds nothing."""
    query = (
        sa.select(
            Fileset.storage_id,
            sa.func.sum(Fileset.allocated_bytes),
            sa.func.sum(Fileset.used_bytes),
        )
        .where(Fileset.owner_user == user, Fileset.state != FilesetState.RELEASED)
        .group_by(Fileset.storage_id)
    )
    return {
        storage_id: Usage(allocated_bytes=int(allocated), used_bytes=int(used))
        for storage_id, allocated, used in await session.execute(query)
    }


async def limits_of(session: AsyncSession, user: str) -> dict[str | None, int]:
    query = sa.select(UserLimit).where(UserLimit.user == user)
    return {row.storage_id: row.allocation_limit_bytes for row in await session.scalars(query)}


def storage_limit(config: ClusterConfig, storage_id: str, limits: dict[str | None, int]) -> int:
    override = limits.get(storage_id)
    if override is not None:
        return override
    return config.storage(storage_id).default_user_allocation_limit_bytes or 0


def total_limit(config: ClusterConfig, limits: dict[str | None, int]) -> int:
    override = limits.get(None)
    if override is not None:
        return override
    return config.limits.user_total_cache_allocation_bytes
