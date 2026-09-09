"""Draining a storage.

Drain is runtime state, not topology: it lives in the database so an admin can set it
without a config rollout, and every scheduler pass and admission reads it from there.
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import NotFound
from stashd.domain.identity import Principal
from stashd.models import StorageState
from stashd.services import audit


async def set_drained(
    session: AsyncSession,
    *,
    cluster: ClusterConfig,
    clock: Clock,
    actor: Principal,
    storage_id: str,
    drained: bool,
) -> bool:
    """Idempotent: draining a drained storage is the same answer, not a conflict."""
    try:
        cluster.storage(storage_id)
    except KeyError:
        raise NotFound(f"no storage named {storage_id}", storage=storage_id) from None

    state = await session.get(StorageState, storage_id)
    if state is None:
        state = StorageState(storage_id=storage_id, drained=drained)
        session.add(state)
    else:
        state.drained = drained
    audit.record(
        session,
        clock,
        actor=actor,
        subject_user=actor.username,
        object_type="storage",
        object_id=storage_id,
        action="drain" if drained else "undrain",
    )
    await session.commit()
    return drained


async def drained_storages(session: AsyncSession) -> frozenset[str]:
    rows = await session.execute(
        sa.select(StorageState.storage_id).where(StorageState.drained.is_(True))
    )
    return frozenset(rows.scalars())
