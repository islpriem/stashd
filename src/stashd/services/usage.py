"""Reconciling what a fileset holds with what the record says.

Plain POSIX cannot enforce a directory quota, so an overrun is only visible
afterwards. This is what makes it visible: the record is corrected, and a fileset over
its allocation is flagged, which blocks the owner's next allocation.
"""

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.usage import Measurable, UsageSource
from stashd.domain.clock import Clock
from stashd.domain.filesets import FilesetState
from stashd.domain.storage import Owner
from stashd.models import Fileset

logger = structlog.get_logger()


async def reconcile_storage(
    session: AsyncSession, *, storage_id: str, usage: UsageSource, clock: Clock
) -> int:
    """Measure every live fileset on one storage. Returns how many were measured."""
    rows = list(
        await session.scalars(
            sa.select(Fileset).where(
                Fileset.storage_id == storage_id,
                Fileset.state.notin_([FilesetState.RELEASED, FilesetState.CREATING]),
            )
        )
    )
    if not rows:
        return 0

    measured = await usage.fileset_usage(
        storage_id,
        [
            Measurable(
                fileset_id=row.id,
                name=row.name,
                owner=Owner(user=row.owner_user, uid=row.owner_uid, gid=row.owner_gid),
                path=row.path,
            )
            for row in rows
        ],
    )
    now = clock.now()
    for row in rows:
        found = measured.get(row.id)
        if found is None:
            continue
        row.used_bytes = found.used_bytes
        row.file_count = found.file_count
        row.used_bytes_at = now
        was = row.over_allocation
        row.over_allocation = found.used_bytes > row.allocated_bytes
        if row.over_allocation != was:
            logger.info(
                "usage.over_allocation" if row.over_allocation else "usage.within_allocation",
                fileset_id=row.id,
                user=row.owner_user,
                storage_id=storage_id,
                used_bytes=row.used_bytes,
                allocated_bytes=row.allocated_bytes,
            )
    await session.commit()
    return len(measured)
