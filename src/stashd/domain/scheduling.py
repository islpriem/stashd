"""The dispatch walk.

Pure: the scheduler service hands in the ordered queue and the load it measured, and gets
back the transfers to dispatch with the bandwidth limit each one should be given. A
transfer that no limit has room for is skipped, never a barrier for the ones behind it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from stashd.domain.routes import Route
from stashd.domain.transfers import TransferKind


@dataclass(frozen=True, slots=True)
class QueuedTransfer:
    id: int
    user: str
    kind: TransferKind
    route: Route
    bytes_total: int
    submitted_at: datetime

    @property
    def storages(self) -> frozenset[str]:
        """Both ends take a per-storage slot; a release has the same storage at both."""
        return frozenset({self.route.source, self.route.target})


@dataclass(frozen=True, slots=True)
class ConcurrencyLimits:
    global_: int
    per_storage: int
    per_user: int
    per_route: int


@dataclass(frozen=True, slots=True)
class BandwidthLimits:
    per_route_aggregate: int
    max_per_transfer: int
    min_per_transfer: int


@dataclass
class Load:
    """How many transfers are running right now, by scope."""

    total: int = 0
    per_storage: dict[str, int] = field(default_factory=dict)
    per_user: dict[str, int] = field(default_factory=dict)
    per_route: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Dispatch:
    transfer_id: int
    bwlimit_bytes_per_s: int | None


def bandwidth_share(active_on_route: int, limits: BandwidthLimits) -> int:
    """The aggregate split over the transfers that would then be running, clamped."""
    share = limits.per_route_aggregate // (active_on_route + 1)
    return max(min(share, limits.max_per_transfer), limits.min_per_transfer)


def plan_dispatch(
    queue: Sequence[QueuedTransfer],
    load: Load,
    limits: ConcurrencyLimits,
    bandwidth: BandwidthLimits,
    *,
    drained: frozenset[str],
) -> list[Dispatch]:
    total = load.total
    per_storage = dict(load.per_storage)
    per_user = dict(load.per_user)
    per_route = dict(load.per_route)
    plan: list[Dispatch] = []

    for transfer in queue:
        route = str(transfer.route)
        if transfer.storages & drained:
            continue
        if total >= limits.global_:
            continue
        if any(per_storage.get(s, 0) >= limits.per_storage for s in transfer.storages):
            continue
        if per_user.get(transfer.user, 0) >= limits.per_user:
            continue
        if transfer.kind.moves_data and per_route.get(route, 0) >= limits.per_route:
            continue

        bwlimit = (
            bandwidth_share(per_route.get(route, 0), bandwidth)
            if transfer.kind.moves_data
            else None
        )
        plan.append(Dispatch(transfer_id=transfer.id, bwlimit_bytes_per_s=bwlimit))

        total += 1
        per_user[transfer.user] = per_user.get(transfer.user, 0) + 1
        for storage in transfer.storages:
            per_storage[storage] = per_storage.get(storage, 0) + 1
        if transfer.kind.moves_data:
            per_route[route] = per_route.get(route, 0) + 1

    return plan
