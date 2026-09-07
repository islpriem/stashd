"""The controller-to-daemon wire. Peer token only; never a MUNGE credential."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from stashd.schemas.base import Wire


class Owner(Wire):
    """Whose data this is. The controller sends what the credential proved."""

    user: str
    uid: int
    gid: int


class CreateFileset(Wire):
    storage_id: str
    name: str
    owner: Owner
    allocation_bytes: int


class DeleteFileset(Wire):
    storage_id: str
    name: str
    owner: Owner
    path: str


class SetQuota(Wire):
    storage_id: str
    name: str
    owner: Owner
    path: str
    allocation_bytes: int


class FilesetLocation(Wire):
    storage_id: str
    name: str
    path: str


class ClusterConfigDocument(Wire):
    """What a daemon fetches: the text the controller loaded, and what identifies it."""

    revision: int
    content_hash: str
    text: str


class Probe(Wire):
    """Ask a source daemon what is at a path, as the user."""

    storage_id: str
    path: str
    owner: Owner


class ProbeResult(Wire):
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    bytes_total: int
    file_count: int
    complete: bool


class Prepare(Wire):
    """Ask a target daemon to make the destination ready. Idempotent."""

    storage_id: str
    name: str
    owner: Owner
    allocation_bytes: int


class Endpoint(Wire):
    path: str
    host: str | None = None
    user: str | None = None


class StartTask(Wire):
    """Ask the source daemon to move the data."""

    transfer_id: int
    storage_id: str
    source_path: str
    owner: Owner
    target: Endpoint
    bwlimit_bytes_per_s: int | None = None
    delete: bool = False


class TaskState(Wire):
    task_id: str
    transfer_id: int
    state: str
    bytes_done: int = 0
    files_done: int = 0
    failure: str | None = None
    message: str = ""


class TransferEvent(Wire):
    """What a daemon reports about a transfer it is running.

    ``sequence`` is what orders them: events arrive duplicated and out of order, and a
    transfer must never move backwards because one was late.
    """

    transfer_id: int
    sequence: int = Field(ge=1)
    kind: Literal["started", "progress", "finished", "failed"]
    daemon_id: str
    at: datetime
    bytes_done: int = 0
    files_done: int = 0
    failure: str | None = None
    message: str = ""


class EventAccepted(Wire):
    transfer_id: int
    applied: bool
    state: str


class Registration(Wire):
    """What a daemon tells the controller when it starts and while it runs."""

    daemon_id: str
    storages: list[str]
    config_revision: int
    version: str


class Registered(Wire):
    daemon_id: str
    config_revision: int
    # True when the controller has moved on and the daemon should fetch again.
    refresh_needed: bool
