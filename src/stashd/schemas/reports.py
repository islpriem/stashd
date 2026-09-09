"""Report responses."""

from stashd.schemas.base import Wire


class UsageGroup(Wire):
    key: str
    bytes_transferred: int
    transfers: int
    succeeded: int
    success_rate: float
    mean_queue_wait_seconds: float
    p95_queue_wait_seconds: float
    mean_throughput_bytes_per_s: float


class UsageReport(Wire):
    group_by: str
    since: str | None
    until: str | None
    groups: list[UsageGroup]


class AllocationRow(Wire):
    user: str
    storage_id: str
    allocated_bytes: int
    used_bytes: int
    filesets: int
    limit_bytes: int


class Offender(Wire):
    user: str
    storage_id: str
    fileset: str
    allocated_bytes: int
    used_bytes: int


class AllocationReport(Wire):
    rows: list[AllocationRow]
    offenders: list[Offender]
