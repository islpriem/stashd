"""Allocation and limit responses."""

from stashd.schemas.base import Wire


class AllocationTotals(Wire):
    limit_bytes: int
    allocated_bytes: int
    used_bytes: int
    free_bytes: int


class StorageAllocation(AllocationTotals):
    storage_id: str


class Allocations(Wire):
    user: str
    total: AllocationTotals
    storages: list[StorageAllocation]


class Limit(Wire):
    user: str
    storage_id: str | None
    allocation_limit_bytes: int


class Limits(Wire):
    limits: list[Limit]
