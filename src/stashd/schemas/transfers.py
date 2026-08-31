"""Transfer responses."""

from datetime import datetime

from stashd.domain.transfers import TransferKind, TransferState
from stashd.schemas.base import Wire


class Transfer(Wire):
    id: int
    kind: TransferKind
    user: str
    fileset_id: int
    peer_ref: str | None
    state: TransferState
    route: str
    bytes_total: int
    bytes_done: int
    files_total: int | None
    files_done: int | None
    executing_daemon_id: str | None
    bwlimit_bytes_per_s: int | None
    attempt: int
    error_code: str | None
    error_detail: str | None
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Transfers(Wire):
    transfers: list[Transfer]
    next_cursor: str | None
