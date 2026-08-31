"""Fileset responses. Sizes are integer bytes, times are RFC 3339 UTC."""

from datetime import datetime

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.schemas.base import Wire


class Fileset(Wire):
    id: int
    name: str
    reference: str
    owner_user: str
    storage_id: str
    kind: FilesetKind
    state: FilesetState
    path: str
    allocated_bytes: int
    used_bytes: int
    used_bytes_at: datetime | None
    file_count: int | None
    over_allocation: bool
    source: str | None
    created_at: datetime
    warm_started_at: datetime | None
    warm_finished_at: datetime | None
    last_flushed_at: datetime | None
    last_flush_target: str | None
    released_at: datetime | None
    last_transfer_id: int | None


class Filesets(Wire):
    filesets: list[Fileset]
