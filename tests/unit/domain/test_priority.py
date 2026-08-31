"""Queue ordering."""

from datetime import UTC, datetime, timedelta

from stashd.domain.fairshare import FairShareAccount
from stashd.domain.priority import DecayingFairShare, PriorityPolicy, order_queue
from stashd.domain.routes import Route
from stashd.domain.scheduling import QueuedTransfer
from stashd.domain.transfers import TransferKind

HALF_LIFE = timedelta(days=7)
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
ROUTE = Route("HOT1", "LOC2HOT")


def queued(transfer_id: int, user: str, submitted: datetime = T0) -> QueuedTransfer:
    return QueuedTransfer(
        id=transfer_id,
        user=user,
        kind=TransferKind.WARM,
        route=ROUTE,
        bytes_total=1024,
        submitted_at=submitted,
    )


def accounts(**points: float) -> dict[str, FairShareAccount]:
    return {
        user: FairShareAccount(user=user, points=value, decayed_at=T0)
        for user, value in points.items()
    }


def test_the_user_with_fewer_points_goes_first() -> None:
    queue = [queued(1, "heavy"), queued(2, "light")]

    ordered = order_queue(
        queue, accounts(heavy=100.0, light=1.0), DecayingFairShare(HALF_LIFE), T0
    )

    assert [transfer.id for transfer in ordered] == [2, 1]


def test_equal_points_fall_back_to_submission_time_then_id() -> None:
    queue = [
        queued(3, "a", T0 + timedelta(seconds=1)),
        queued(2, "b", T0),
        queued(1, "c", T0),
    ]

    ordered = order_queue(
        queue, accounts(a=1.0, b=1.0, c=1.0), DecayingFairShare(HALF_LIFE), T0
    )

    assert [transfer.id for transfer in ordered] == [1, 2, 3]


def test_an_old_debt_decays_until_a_newer_one_outweighs_it() -> None:
    later = T0 + 6 * HALF_LIFE
    queue = [queued(1, "long_ago"), queued(2, "recently")]
    debts = {
        "long_ago": FairShareAccount(user="long_ago", points=64.0, decayed_at=T0),
        "recently": FairShareAccount(user="recently", points=2.0, decayed_at=later),
    }
    policy = DecayingFairShare(HALF_LIFE)

    assert [t.id for t in order_queue(queue, debts, policy, T0)] == [2, 1]
    assert [t.id for t in order_queue(queue, debts, policy, later)] == [1, 2]


def test_a_user_without_an_account_is_treated_as_owing_nothing() -> None:
    queue = [queued(1, "known"), queued(2, "newcomer")]

    ordered = order_queue(queue, accounts(known=5.0), DecayingFairShare(HALF_LIFE), T0)

    assert [transfer.id for transfer in ordered] == [2, 1]


def test_the_policy_is_injectable() -> None:
    class LastSubmittedFirst:
        def key(
            self, transfer: QueuedTransfer, account: FairShareAccount, now: datetime
        ) -> tuple[float, datetime, int]:
            return (0.0, T0, -transfer.id)

    policy: PriorityPolicy = LastSubmittedFirst()
    queue = [queued(1, "a"), queued(2, "b")]

    assert [t.id for t in order_queue(queue, accounts(a=0.0, b=99.0), policy, T0)] == [2, 1]
