"""Admission control: the four allocation checks."""

import pytest

from stashd.domain.allocation import (
    AllocationRequest,
    AllocationState,
    admit,
    allocation_for_source,
    check_resize,
)
from stashd.domain.errors import ErrorCode, StashError

GIB = 1024**3


def state(**overrides: object) -> AllocationState:
    defaults = {
        "user_allocated_on_storage": 0,
        "user_limit_on_storage": 100 * GIB,
        "user_allocated_on_caches": 0,
        "user_total_limit": 250 * GIB,
        "storage_allocated": 0,
        "storage_capacity_bytes": 1000 * GIB,
        "fill_limit": 0.95,
        "user_fileset_count": 0,
        "max_filesets_per_user": 50,
        "over_allocation_filesets": (),
        "storage_drained": False,
    }
    return AllocationState(**{**defaults, **overrides})  # type: ignore[arg-type]


def request(size: int = 21 * GIB) -> AllocationRequest:
    return AllocationRequest(user="mmustermann", storage_id="LOC2HOT", requested_bytes=size)


def raised(req: AllocationRequest, st: AllocationState) -> StashError:
    with pytest.raises(StashError) as excinfo:
        admit(req, st)
    return excinfo.value


class TestAdmission:
    def test_a_request_that_fits_is_admitted(self) -> None:
        admit(request(), state())

    def test_allocation_counts_in_full_regardless_of_usage(self) -> None:
        """A fileset holds its whole reservation, so 90 GiB allocated leaves 10 GiB free."""
        error = raised(request(21 * GIB), state(user_allocated_on_storage=90 * GIB))

        assert error.code is ErrorCode.ALLOCATION_LIMIT_EXCEEDED
        assert error.details["free_bytes"] == 10 * GIB

    def test_per_storage_limit_carries_its_numbers(self) -> None:
        error = raised(request(21 * GIB), state(user_allocated_on_storage=88 * GIB))

        assert error.code is ErrorCode.ALLOCATION_LIMIT_EXCEEDED
        assert error.details == {
            "required_bytes": 21 * GIB,
            "free_bytes": 12 * GIB,
            "limit_bytes": 100 * GIB,
            "scope": "storage",
            "storage": "LOC2HOT",
        }
        assert "21.0 GiB" in error.message
        assert "12.0 GiB" in error.message
        assert "100.0 GiB" in error.message

    def test_cluster_wide_limit_carries_its_numbers(self) -> None:
        error = raised(request(21 * GIB), state(user_allocated_on_caches=240 * GIB))

        assert error.code is ErrorCode.TOTAL_ALLOCATION_LIMIT_EXCEEDED
        assert error.details == {
            "required_bytes": 21 * GIB,
            "free_bytes": 10 * GIB,
            "limit_bytes": 250 * GIB,
            "scope": "total",
        }

    def test_storage_capacity_uses_the_fill_limit(self) -> None:
        error = raised(request(100 * GIB), state(storage_allocated=900 * GIB))

        assert error.code is ErrorCode.STORAGE_FULL
        assert error.details["free_bytes"] == 50 * GIB
        assert error.details["capacity_bytes"] == 1000 * GIB
        assert error.details["fill_limit"] == 0.95
        assert error.details["storage"] == "LOC2HOT"

    def test_exactly_filling_a_limit_is_admitted(self) -> None:
        admit(request(10 * GIB), state(user_allocated_on_storage=90 * GIB))

    def test_the_per_storage_limit_is_checked_before_the_cluster_wide_one(self) -> None:
        error = raised(
            request(21 * GIB),
            state(user_allocated_on_storage=90 * GIB, user_allocated_on_caches=240 * GIB),
        )

        assert error.code is ErrorCode.ALLOCATION_LIMIT_EXCEEDED

    def test_too_many_filesets(self) -> None:
        error = raised(request(), state(user_fileset_count=50))

        assert error.code is ErrorCode.TOO_MANY_FILESETS
        assert error.details == {"count": 50, "limit": 50}

    def test_an_over_allocated_fileset_blocks_further_allocation(self) -> None:
        error = raised(request(), state(over_allocation_filesets=("results", "other")))

        assert error.code is ErrorCode.OVER_ALLOCATION
        assert error.details["filesets"] == ["results", "other"]
        assert "results" in error.message

    def test_a_drained_storage_refuses_new_filesets(self) -> None:
        error = raised(request(), state(storage_drained=True))

        assert error.code is ErrorCode.STORAGE_DRAINED
        assert error.details["storage"] == "LOC2HOT"

    def test_drain_and_over_allocation_are_checked_before_the_numbers(self) -> None:
        error = raised(request(10_000 * GIB), state(storage_drained=True))

        assert error.code is ErrorCode.STORAGE_DRAINED

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_zero_or_negative_allocation_is_refused(self, size: int) -> None:
        assert raised(request(size), state()).code is ErrorCode.CONFLICT


class TestAllocationForSource:
    def test_the_headroom_is_applied_and_rounded_up(self) -> None:
        assert allocation_for_source(20 * GIB, headroom=1.05) == 21 * GIB
        assert allocation_for_source(3, headroom=1.05) == 4

    def test_a_larger_request_wins(self) -> None:
        assert allocation_for_source(20 * GIB, headroom=1.05, requested=30 * GIB) == 30 * GIB

    def test_a_smaller_request_does_not_shrink_the_allocation(self) -> None:
        assert allocation_for_source(20 * GIB, headroom=1.05, requested=1 * GIB) == 21 * GIB

    def test_an_empty_source_still_reserves_something(self) -> None:
        assert allocation_for_source(0, headroom=1.05) == 1


class TestResize:
    def test_growing_returns_the_delta_to_admit(self) -> None:
        assert (
            check_resize(current=10 * GIB, used=5 * GIB, new=15 * GIB, force=False) == 5 * GIB
        )

    def test_shrinking_above_usage_is_allowed(self) -> None:
        assert (
            check_resize(current=10 * GIB, used=5 * GIB, new=6 * GIB, force=False) == -4 * GIB
        )

    def test_shrinking_below_usage_is_refused(self) -> None:
        with pytest.raises(StashError) as excinfo:
            check_resize(current=10 * GIB, used=5 * GIB, new=4 * GIB, force=False)

        assert excinfo.value.code is ErrorCode.CONFLICT
        assert excinfo.value.details == {"used_bytes": 5 * GIB, "requested_bytes": 4 * GIB}

    def test_resizing_to_nothing_is_refused_even_with_force(self) -> None:
        with pytest.raises(StashError) as excinfo:
            check_resize(current=10 * GIB, used=0, new=0, force=True)

        assert excinfo.value.code is ErrorCode.CONFLICT

    def test_an_admin_may_force_a_shrink_below_usage(self) -> None:
        assert check_resize(current=10 * GIB, used=5 * GIB, new=4 * GIB, force=True) == -6 * GIB
