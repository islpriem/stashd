"""Decaying fair-share accounting.

Points grow with transferred volume and halve every half-life. Decay is computed lazily
from ``decayed_at`` whenever an account is read or charged, so no periodic rewrite of the
table is needed.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from stashd.domain.transfers import TransferKind

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class FairShareAccount:
    user: str
    points: float
    decayed_at: datetime


def decayed(account: FairShareAccount, now: datetime, half_life: timedelta) -> FairShareAccount:
    elapsed = (now - account.decayed_at).total_seconds()
    if elapsed <= 0:
        return account
    factor = 2 ** (-elapsed / half_life.total_seconds())
    return replace(account, points=account.points * factor, decayed_at=now)


def points_for_bytes(kind: TransferKind, size_bytes: int, *, points_per_gib: float) -> float:
    """A release moves nothing and is charged nothing; everything else pays per GiB."""
    if not kind.moves_data:
        return 0.0
    return math.ceil(size_bytes / GIB) * points_per_gib


def with_points(
    account: FairShareAccount, delta: float, now: datetime, half_life: timedelta
) -> FairShareAccount:
    """Decay first, then charge or refund. Points never go negative."""
    current = decayed(account, now, half_life)
    return replace(current, points=max(current.points + delta, 0.0))
