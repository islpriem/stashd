"""The p95 the usage report quotes."""

import math

import pytest

from stashd.services.reports import _percentile


@pytest.mark.parametrize("count", [1, 2, 3, 10, 19, 20, 21, 100, 1000])
def test_p95_is_the_nearest_rank(count: int) -> None:
    """Nearest-rank: the ceil(0.95 n)-th value, so it is always one that was observed."""
    values = [float(value) for value in range(1, count + 1)]

    assert _percentile(values, 0.95) == float(math.ceil(0.95 * count))


def test_it_never_invents_a_value_that_was_not_seen() -> None:
    values = [1.0, 2.0, 100.0]

    assert _percentile(values, 0.95) in values


def test_nothing_observed_is_zero_not_an_error() -> None:
    assert _percentile([], 0.95) == 0.0


def test_one_observation_is_its_own_percentile() -> None:
    assert _percentile([7.5], 0.95) == 7.5
