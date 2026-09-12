"""Admission control.

Allocation is pessimistic: a fileset counts in full against every limit from ``CREATING``
until ``RELEASED``, whatever it actually uses. Nothing here reads a database — the
service gathers the sums into an :class:`AllocationState` and the decision is pure.

Order of the checks. The two gates come first because they are unconditional and their
message is more useful than a byte count: a drained storage, then an outstanding
over-allocation, then the fileset count, then the user's limit on the storage, the user's
limit across all caches, and last the storage's capacity.
"""

import math
from dataclasses import dataclass

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.sizes import format_bytes


class AllocationLimitExceeded(StashError):
    code = ErrorCode.ALLOCATION_LIMIT_EXCEEDED


class TotalAllocationLimitExceeded(StashError):
    code = ErrorCode.TOTAL_ALLOCATION_LIMIT_EXCEEDED


class StorageFull(StashError):
    code = ErrorCode.STORAGE_FULL


class OverAllocation(StashError):
    code = ErrorCode.OVER_ALLOCATION


class TooManyFilesets(StashError):
    code = ErrorCode.TOO_MANY_FILESETS


class StorageDrained(StashError):
    code = ErrorCode.STORAGE_DRAINED


class Conflict(StashError):
    code = ErrorCode.CONFLICT


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    user: str
    storage_id: str
    requested_bytes: int


@dataclass(frozen=True, slots=True)
class AllocationState:
    """The sums an admission decision needs, as of the transaction that gathered them."""

    user_allocated_on_storage: int
    user_limit_on_storage: int
    user_allocated_on_caches: int
    user_total_limit: int
    storage_allocated: int
    storage_capacity_bytes: int
    fill_limit: float
    user_fileset_count: int
    max_filesets_per_user: int
    over_allocation_filesets: tuple[str, ...]
    storage_drained: bool


def admit(request: AllocationRequest, state: AllocationState) -> None:
    """Raise the first violated rule, or return for a request that fits."""
    if request.requested_bytes <= 0:
        raise Conflict(
            f"an allocation must be at least one byte, not {request.requested_bytes}",
            requested_bytes=request.requested_bytes,
        )
    if state.storage_drained:
        raise StorageDrained(
            f"{request.storage_id} is drained: no new filesets while it is",
            storage=request.storage_id,
        )
    if state.over_allocation_filesets:
        offenders = ", ".join(state.over_allocation_filesets)
        raise OverAllocation(
            # Not "resize": growing a fileset is itself an allocating operation and
            # lands right back here. What works is getting under the
            # reservation again, or giving the fileset up.
            f"your fileset(s) {offenders} use more than they reserved; "
            "delete what is inside them or release them before allocating more",
            filesets=list(state.over_allocation_filesets),
        )
    if state.user_fileset_count >= state.max_filesets_per_user:
        raise TooManyFilesets(
            f"you already own {state.user_fileset_count} filesets, "
            f"the limit is {state.max_filesets_per_user}",
            count=state.user_fileset_count,
            limit=state.max_filesets_per_user,
        )

    needed = request.requested_bytes
    free_on_storage = state.user_limit_on_storage - state.user_allocated_on_storage
    if needed > free_on_storage:
        raise AllocationLimitExceeded(
            f"{format_bytes(needed)} needed; {format_bytes(max(free_on_storage, 0))} of your "
            f"{format_bytes(state.user_limit_on_storage)} limit "
            f"on {request.storage_id} is free",
            required_bytes=needed,
            free_bytes=max(free_on_storage, 0),
            limit_bytes=state.user_limit_on_storage,
            scope="storage",
            storage=request.storage_id,
        )

    free_in_total = state.user_total_limit - state.user_allocated_on_caches
    if needed > free_in_total:
        raise TotalAllocationLimitExceeded(
            f"{format_bytes(needed)} needed; {format_bytes(max(free_in_total, 0))} of your "
            f"{format_bytes(state.user_total_limit)} limit across all caches is free",
            required_bytes=needed,
            free_bytes=max(free_in_total, 0),
            limit_bytes=state.user_total_limit,
            scope="total",
        )

    usable = int(state.storage_capacity_bytes * state.fill_limit)
    free_on_capacity = usable - state.storage_allocated
    if needed > free_on_capacity:
        raise StorageFull(
            f"{request.storage_id} has {format_bytes(max(free_on_capacity, 0))} allocatable "
            f"left of {format_bytes(usable)}; {format_bytes(needed)} needed",
            required_bytes=needed,
            free_bytes=max(free_on_capacity, 0),
            capacity_bytes=state.storage_capacity_bytes,
            fill_limit=state.fill_limit,
            storage=request.storage_id,
        )


def allocation_for_source(
    measured_bytes: int, *, headroom: float, requested: int | None = None
) -> int:
    """A cached fileset reserves its source size plus headroom, or more if asked."""
    with_headroom = max(math.ceil(measured_bytes * headroom), 1)
    return max(with_headroom, requested) if requested is not None else with_headroom


def check_resize(*, current: int, used: int, new: int, force: bool) -> int:
    """The delta a resize would add; growing it is admitted separately."""
    if new <= 0:
        raise Conflict(
            f"an allocation must be at least one byte, not {new}", requested_bytes=new
        )
    if new < used and not force:
        raise Conflict(
            f"the fileset already uses {format_bytes(used)}; "
            f"an admin can force a shrink to {format_bytes(new)}",
            used_bytes=used,
            requested_bytes=new,
        )
    return new - current
