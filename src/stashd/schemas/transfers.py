"""Transfer responses."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

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


class FilesetRef(Wire):
    storage: str
    fileset: str


class PathRef(Wire):
    storage: str
    path: str


class SubmitRelease(Wire):
    """Deleting a fileset. An output fileset needs ``discard`` to say so."""

    kind: Literal[TransferKind.RELEASE]
    target: FilesetRef
    discard: bool = False
    user: str | None = None


class SubmitWarm(Wire):
    """A source path into a cached fileset."""

    kind: Literal[TransferKind.WARM]
    source: PathRef
    target: FilesetRef
    size_bytes: int | None = Field(default=None, gt=0)
    refresh: bool = False
    dry_run: bool = False
    user: str | None = None


class SubmitFlush(Wire):
    """A fileset written back out to a source storage."""

    kind: Literal[TransferKind.FLUSH]
    source: FilesetRef
    target: PathRef
    keep: bool = False
    dry_run: bool = False
    user: str | None = None


Submit = Annotated[SubmitFlush | SubmitRelease | SubmitWarm, Field(discriminator="kind")]


class Preflight(Wire):
    """What a dry run answers: everything the submission would have decided."""

    kind: TransferKind
    source: str
    target: str
    path: str
    route: str
    bytes_total: int
    file_count: int
    allocation_bytes: int
    refresh: bool
    estimated_start_seconds: int
    estimated_duration_seconds: int
