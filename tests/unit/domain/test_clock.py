"""The only place that reads the wall clock."""

from datetime import UTC

from stashd.domain.clock import SystemClock


def test_the_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()

    assert now.tzinfo is UTC
