"""Queue ordering."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol

from stashd.domain.fairshare import FairShareAccount, decayed
from stashd.domain.scheduling import QueuedTransfer


class PriorityPolicy(Protocol):
    """Decides the order of the queue. Decaying fair share is the only MVP policy."""

    def key(
        self, transfer: QueuedTransfer, account: FairShareAccount, now: datetime
    ) -> tuple[float, datetime, int]: ...


class DecayingFairShare:
    def __init__(self, half_life: timedelta) -> None:
        self._half_life = half_life

    def key(
        self, transfer: QueuedTransfer, account: FairShareAccount, now: datetime
    ) -> tuple[float, datetime, int]:
        points = decayed(account, now, self._half_life).points
        return (points, transfer.submitted_at, transfer.id)


def order_queue(
    transfers: Sequence[QueuedTransfer],
    accounts: Mapping[str, FairShareAccount],
    policy: PriorityPolicy,
    now: datetime,
) -> list[QueuedTransfer]:
    def account_of(user: str) -> FairShareAccount:
        return accounts.get(user) or FairShareAccount(user=user, points=0.0, decayed_at=now)

    return sorted(transfers, key=lambda t: policy.key(t, account_of(t.user), now))
