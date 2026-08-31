"""The dispatch walk and its limits."""

from datetime import UTC, datetime

from stashd.domain.routes import Route
from stashd.domain.scheduling import (
    BandwidthLimits,
    ConcurrencyLimits,
    Load,
    QueuedTransfer,
    plan_dispatch,
)
from stashd.domain.transfers import TransferKind

T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
MBIT = 1000**2 // 8
BANDWIDTH = BandwidthLimits(
    per_route_aggregate=800 * MBIT, max_per_transfer=400 * MBIT, min_per_transfer=50 * MBIT
)
LIMITS = ConcurrencyLimits(global_=20, per_storage=8, per_user=2, per_route=4)
WARM_ROUTE = Route("HOT1", "LOC2HOT")


def queued(
    transfer_id: int,
    *,
    user: str = "mmustermann",
    route: Route = WARM_ROUTE,
    kind: TransferKind = TransferKind.WARM,
) -> QueuedTransfer:
    return QueuedTransfer(
        id=transfer_id, user=user, kind=kind, route=route, bytes_total=1024, submitted_at=T0
    )


def dispatched(*args: object, **kwargs: object) -> list[int]:
    return [dispatch.transfer_id for dispatch in plan_dispatch(*args, **kwargs)]  # type: ignore[arg-type]


class TestLimits:
    def test_an_empty_queue_dispatches_nothing(self) -> None:
        assert plan_dispatch([], Load(), LIMITS, BANDWIDTH, drained=frozenset()) == []

    def test_the_global_limit_stops_dispatching(self) -> None:
        queue = [queued(1), queued(2, user="other")]
        load = Load(total=19)

        assert dispatched(queue, load, LIMITS, BANDWIDTH, drained=frozenset()) == [1]

    def test_the_per_user_limit_only_blocks_that_user(self) -> None:
        queue = [queued(1), queued(2), queued(3), queued(4, user="other")]

        assert dispatched(queue, Load(), LIMITS, BANDWIDTH, drained=frozenset()) == [1, 2, 4]

    def test_the_per_route_limit_blocks_only_that_route(self) -> None:
        limits = ConcurrencyLimits(global_=20, per_storage=8, per_user=8, per_route=1)
        queue = [queued(1), queued(2), queued(3, route=Route("HOT1", "OTHER"))]

        assert dispatched(queue, Load(), limits, BANDWIDTH, drained=frozenset()) == [1, 3]

    def test_the_per_storage_limit_counts_both_ends_of_a_route(self) -> None:
        limits = ConcurrencyLimits(global_=20, per_storage=1, per_user=8, per_route=8)
        queue = [
            queued(1),
            queued(2, route=Route("OTHER", "LOC2HOT")),
            queued(3, route=Route("A", "B")),
        ]

        assert dispatched(queue, Load(), limits, BANDWIDTH, drained=frozenset()) == [1, 3]

    def test_a_blocked_transfer_is_skipped_and_never_a_barrier(self) -> None:
        limits = ConcurrencyLimits(global_=20, per_storage=8, per_user=1, per_route=8)
        queue = [queued(1, user="a"), queued(2, user="a"), queued(3, user="b")]

        assert dispatched(queue, Load(), limits, BANDWIDTH, drained=frozenset()) == [1, 3]

    def test_a_drained_storage_takes_no_new_dispatches(self) -> None:
        queue = [queued(1), queued(2, route=Route("HOT1", "OTHER"))]

        assert dispatched(queue, Load(), LIMITS, BANDWIDTH, drained=frozenset({"LOC2HOT"})) == [
            2
        ]

    def test_a_release_takes_a_storage_slot_but_no_route_slot(self) -> None:
        limits = ConcurrencyLimits(global_=20, per_storage=1, per_user=8, per_route=8)
        release = queued(1, route=Route("LOC2HOT", "LOC2HOT"), kind=TransferKind.RELEASE)
        queue = [release, queued(2)]

        assert dispatched(queue, Load(), limits, BANDWIDTH, drained=frozenset()) == [1]

    def test_a_release_gets_no_bandwidth_limit(self) -> None:
        release = queued(1, route=Route("LOC2HOT", "LOC2HOT"), kind=TransferKind.RELEASE)

        assert (
            plan_dispatch([release], Load(), LIMITS, BANDWIDTH, drained=frozenset())[
                0
            ].bwlimit_bytes_per_s
            is None
        )


class TestBandwidth:
    def test_the_route_aggregate_is_split_over_the_active_transfers(self) -> None:
        load = Load(per_route={"HOT1->LOC2HOT": 1})

        plan = plan_dispatch([queued(1)], load, LIMITS, BANDWIDTH, drained=frozenset())

        assert plan[0].bwlimit_bytes_per_s == 800 * MBIT // 2

    def test_each_dispatch_in_one_pass_sees_the_earlier_ones(self) -> None:
        queue = [queued(1, user="a"), queued(2, user="b"), queued(3, user="c")]

        plan = plan_dispatch(queue, Load(), LIMITS, BANDWIDTH, drained=frozenset())

        assert [dispatch.bwlimit_bytes_per_s for dispatch in plan] == [
            400 * MBIT,
            400 * MBIT,
            800 * MBIT // 3,
        ]

    def test_the_share_is_clamped_to_the_minimum(self) -> None:
        load = Load(per_route={"HOT1->LOC2HOT": 30})
        limits = ConcurrencyLimits(global_=64, per_storage=64, per_user=64, per_route=64)

        plan = plan_dispatch([queued(1)], load, limits, BANDWIDTH, drained=frozenset())

        assert plan[0].bwlimit_bytes_per_s == 50 * MBIT
