"""Decaying fair share."""

from datetime import UTC, datetime, timedelta

import pytest

from stashd.domain.fairshare import FairShareAccount, decayed, points_for_bytes, with_points
from stashd.domain.transfers import TransferKind

GIB = 1024**3
HALF_LIFE = timedelta(days=7)
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def account(points: float = 0.0, at: datetime = T0) -> FairShareAccount:
    return FairShareAccount(user="mmustermann", points=points, decayed_at=at)


class TestDecay:
    def test_one_half_life_halves_the_points(self) -> None:
        assert decayed(account(100.0), T0 + HALF_LIFE, HALF_LIFE).points == pytest.approx(50.0)

    def test_two_half_lives_quarter_them(self) -> None:
        assert decayed(account(100.0), T0 + 2 * HALF_LIFE, HALF_LIFE).points == pytest.approx(
            25.0
        )

    def test_no_elapsed_time_changes_nothing(self) -> None:
        assert decayed(account(100.0), T0, HALF_LIFE).points == pytest.approx(100.0)

    def test_decay_is_lazy_and_records_when_it_happened(self) -> None:
        original = account(100.0)

        result = decayed(original, T0 + HALF_LIFE, HALF_LIFE)

        assert result.decayed_at == T0 + HALF_LIFE
        assert original.points == 100.0, "the stored account is not mutated"

    def test_decaying_twice_is_the_same_as_decaying_once(self) -> None:
        once = decayed(account(100.0), T0 + HALF_LIFE, HALF_LIFE)
        twice = decayed(once, T0 + HALF_LIFE, HALF_LIFE)

        assert twice.points == pytest.approx(once.points)

    def test_a_clock_that_went_backwards_does_not_inflate_points(self) -> None:
        assert decayed(account(100.0), T0 - HALF_LIFE, HALF_LIFE).points == pytest.approx(100.0)


class TestCharging:
    def test_bytes_are_charged_per_gibibyte_rounded_up(self) -> None:
        assert points_for_bytes(TransferKind.WARM, 20 * GIB, points_per_gib=1.0) == 20.0
        assert points_for_bytes(TransferKind.WARM, 20 * GIB + 1, points_per_gib=1.0) == 21.0

    def test_the_rate_scales_the_charge(self) -> None:
        assert points_for_bytes(TransferKind.FLUSH, 20 * GIB, points_per_gib=0.5) == 10.0

    def test_a_release_is_charged_nothing(self) -> None:
        assert points_for_bytes(TransferKind.RELEASE, 20 * GIB, points_per_gib=1.0) == 0.0

    def test_charging_decays_first_and_then_adds(self) -> None:
        charged = with_points(account(100.0), 10.0, T0 + HALF_LIFE, HALF_LIFE)

        assert charged.points == pytest.approx(60.0)
        assert charged.decayed_at == T0 + HALF_LIFE

    def test_a_refund_subtracts_the_estimate_again(self) -> None:
        estimate = points_for_bytes(TransferKind.WARM, 20 * GIB, points_per_gib=1.0)
        charged = with_points(account(0.0), estimate, T0, HALF_LIFE)

        refunded = with_points(charged, -estimate, T0, HALF_LIFE)

        assert refunded.points == pytest.approx(0.0)

    def test_points_never_go_below_zero(self) -> None:
        assert with_points(account(1.0), -10.0, T0, HALF_LIFE).points == 0.0


def test_asking_about_nobody_is_not_a_query() -> None:
    """The scheduler calls this with whatever is queued, which is often nothing."""
    import asyncio

    from stashd.services.fairshare import accounts_for

    async def ask() -> dict[str, FairShareAccount]:
        return await accounts_for(None, [], None, HALF_LIFE)  # type: ignore[arg-type]

    assert asyncio.run(ask()) == {}
