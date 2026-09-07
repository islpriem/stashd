"""Fair-share points, kept in the database and decayed when they are read."""

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.clock import Clock
from stashd.domain.fairshare import FairShareAccount, decayed, with_points
from stashd.models import FairShareAccountRow


async def accounts_for(
    session: AsyncSession, users: list[str], clock: Clock, half_life: timedelta
) -> dict[str, FairShareAccount]:
    """What each user owes right now. Decay is applied on read, never by a sweep."""
    if not users:
        return {}
    rows = await session.scalars(
        sa.select(FairShareAccountRow).where(FairShareAccountRow.user.in_(users))
    )
    now = clock.now()
    return {
        row.user: decayed(
            FairShareAccount(user=row.user, points=row.points, decayed_at=row.decayed_at),
            now,
            half_life,
        )
        for row in rows
    }


async def charge(
    session: AsyncSession, user: str, points: float, clock: Clock, half_life: timedelta
) -> None:
    """Add to what a user owes, or give it back. Decays first, so it is never inflated."""
    row = await session.get(FairShareAccountRow, user)
    now = clock.now()
    if row is None:
        row = FairShareAccountRow(user=user, points=0.0, decayed_at=now)
        session.add(row)
    updated = with_points(
        FairShareAccount(user=row.user, points=row.points, decayed_at=row.decayed_at),
        points,
        now,
        half_life,
    )
    row.points = updated.points
    row.decayed_at = updated.decayed_at
