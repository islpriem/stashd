"""The cluster config: topology and policy, authored on the controller.

Loaded and validated wholesale — a file with any problem is rejected as a whole, with
every problem reported. Sizes become integer bytes and rates integer bytes per
second at load time; nothing downstream parses a suffix.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from stashd.config._yaml import problems_from, read_yaml_mapping
from stashd.config.errors import ConfigError
from stashd.domain.failures import FailureClass
from stashd.domain.units import parse_duration, parse_rate, parse_size

ROUTE_RE = re.compile(r"^(?P<source>[A-Z][A-Z0-9_-]*)->(?P<target>[A-Z][A-Z0-9_-]*)$")


def _size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"invalid size {value!r}: expected an integer or a string")
    return parse_size(value)


def _rate(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"invalid rate {value!r}: expected an integer or a string")
    return parse_rate(value)


def _duration(value: Any) -> timedelta:
    if not isinstance(value, str):
        raise ValueError(f"invalid duration {value!r}: expected a string such as 30s")
    return parse_duration(value)


def _mode(value: Any) -> int:
    """A POSIX permission, written as it would be typed into chmod: 0700, 750."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"invalid fileset_mode {value!r}: expected e.g. 0700")
    try:
        mode = int(str(value), 8)
    except ValueError:
        raise ValueError(f"invalid fileset_mode {value!r}: expected octal, e.g. 0700") from None
    if not 0 <= mode <= 0o777:
        raise ValueError(f"invalid fileset_mode {value!r}: outside 0000..0777")
    return mode


def _absolute(value: Any) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{value!r} is not an absolute path")
    return path


ByteSize = Annotated[int, BeforeValidator(_size)]
FilesetMode = Annotated[int, BeforeValidator(_mode)]
BytesPerSecond = Annotated[int, BeforeValidator(_rate)]
Duration = Annotated[timedelta, BeforeValidator(_duration)]
AbsolutePath = Annotated[Path, BeforeValidator(_absolute)]
StorageId = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_-]{0,31}$")]
LocationId = StorageId
DaemonId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


class StorageRole(StrEnum):
    SOURCE = "source"
    CACHE = "cache"


class Tier(StrEnum):
    HOT = "hot"
    COLD = "cold"
    ARCHIVE = "archive"


class Driver(StrEnum):
    POSIX = "posix"


class Engine(StrEnum):
    RSYNC = "rsync"


class CliAuth(StrEnum):
    MUNGE = "munge"


class PeerAuth(StrEnum):
    TOKEN = "token"
    MUNGE = "munge"


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Location(Section):
    id: LocationId
    name: str
    enabled: bool = True


class Daemon(Section):
    """A stashd process the controller talks to, and the host its storages are on."""

    id: DaemonId
    url: str
    host: str | None = None

    @model_validator(mode="after")
    def _default_host_to_the_url(self) -> Self:
        if self.host is None:
            object.__setattr__(self, "host", urlparse(self.url).hostname or self.url)
        return self


class Storage(Section):
    id: StorageId
    location: LocationId
    roles: list[StorageRole] = Field(min_length=1)
    tier: Tier
    driver: Driver
    root: AbsolutePath
    fileset_prefix: AbsolutePath
    capacity_bytes: ByteSize | None = Field(default=None, alias="capacity")
    fill_limit: float = Field(default=0.95, gt=0.0, le=1.0)
    default_user_allocation_limit_bytes: ByteSize | None = Field(
        default=None, alias="default_user_allocation_limit"
    )
    daemon: DaemonId
    fileset_mode: FilesetMode = 0o700
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _default_fileset_prefix_to_root(cls, data: Any) -> Any:
        if isinstance(data, dict) and "root" in data and not data.get("fileset_prefix"):
            data = {**data, "fileset_prefix": data["root"]}
        return data

    def has_role(self, role: StorageRole) -> bool:
        return role in self.roles


class Concurrency(Section):
    global_: int = Field(alias="global", gt=0)
    per_storage: int = Field(gt=0)
    per_user: int = Field(gt=0)
    per_route: int = Field(gt=0)


class Bandwidth(Section):
    per_route_aggregate_bytes_per_s: BytesPerSecond = Field(alias="per_route_aggregate", gt=0)
    max_per_transfer_bytes_per_s: BytesPerSecond = Field(alias="max_per_transfer", gt=0)
    min_per_transfer_bytes_per_s: BytesPerSecond = Field(alias="min_per_transfer", gt=0)


class Limits(Section):
    user_total_cache_allocation_bytes: ByteSize = Field(
        alias="user_total_cache_allocation", gt=0
    )
    max_filesets_per_user: int = Field(gt=0)
    queued_transfers_per_user: int = Field(gt=0)
    concurrency: Concurrency
    bandwidth: Bandwidth


class FairShare(Section):
    half_life: Duration
    points_per_gib: float = Field(gt=0.0)


class Scheduling(Section):
    interval: Duration
    fairshare: FairShare
    allocation_headroom: float = Field(ge=1.0)


class Retries(Section):
    count: int = Field(ge=0)
    backoff: Duration
    retry_on: list[FailureClass]


class NominalThroughput(Section):
    default: BytesPerSecond = Field(gt=0)
    routes: dict[str, BytesPerSecond] = Field(default_factory=dict)


class Transfer(Section):
    engine: Engine
    retries: Retries
    progress_poll_interval: Duration
    nominal_throughput: NominalThroughput


class Auth(Section):
    cli: CliAuth
    munge_socket: AbsolutePath
    peer: PeerAuth
    admin_uids: list[int] = Field(default_factory=list)
    admin_gids: list[int | str] = Field(default_factory=list)


class Timeouts(Section):
    """How long the controller waits before it stops believing a daemon."""

    daemon_unreachable: Duration = timedelta(minutes=10)
    drain: Duration = timedelta(minutes=5)


class Retention(Section):
    transfers: Duration
    audit: Duration


class ClusterConfig(Section):
    revision: int = Field(ge=0)
    locations: list[Location]
    daemons: list[Daemon]
    storages: list[Storage]
    limits: Limits
    scheduling: Scheduling
    transfer: Transfer
    auth: Auth
    retention: Retention
    timeouts: Timeouts = Timeouts()

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def daemon(self, daemon_id: str) -> Daemon:
        for daemon in self.daemons:
            if daemon.id == daemon_id:
                return daemon
        raise KeyError(daemon_id)

    def daemon_for(self, storage_id: str) -> Daemon:
        return self.daemon(self.storage(storage_id).daemon)

    def storage(self, storage_id: str) -> Storage:
        for storage in self.storages:
            if storage.id == storage_id:
                return storage
        raise KeyError(storage_id)

    def cache_storages(self) -> list[Storage]:
        return [s for s in self.storages if s.has_role(StorageRole.CACHE)]

    def storages_of(self, daemon_id: str) -> list[Storage]:
        return [s for s in self.storages if s.daemon == daemon_id]

    @model_validator(mode="after")
    def _check_cross_references(self) -> Self:
        problems = [
            *_duplicate_ids("location", (loc.id for loc in self.locations)),
            *_duplicate_ids("storage", (s.id for s in self.storages)),
            *_duplicate_ids("daemon", (d.id for d in self.daemons)),
            *self._storage_problems(),
            *self._nested_root_problems(),
            *self._route_problems(),
        ]
        bandwidth = self.limits.bandwidth
        if bandwidth.min_per_transfer_bytes_per_s > bandwidth.max_per_transfer_bytes_per_s:
            problems.append("limits.bandwidth: min_per_transfer exceeds max_per_transfer")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _storage_problems(self) -> list[str]:
        known_locations = {loc.id for loc in self.locations}
        known_daemons = {daemon.id for daemon in self.daemons}
        problems = []
        for storage in self.storages:
            if storage.location not in known_locations:
                problems.append(
                    f"storage {storage.id!r}: unknown location {storage.location!r}"
                )
            if storage.daemon not in known_daemons:
                problems.append(f"storage {storage.id!r}: unknown daemon {storage.daemon!r}")
            if storage.has_role(StorageRole.CACHE) and storage.capacity_bytes is None:
                problems.append(f"cache storage {storage.id!r} needs a capacity")
        return problems

    def _nested_root_problems(self) -> list[str]:
        problems = []
        for storage in self.storages:
            for other in self.storages_of(storage.daemon):
                if other.id != storage.id and _is_inside(storage.root, other.root):
                    problems.append(
                        f"storage {storage.id!r} root {str(storage.root)!r} nests inside "
                        f"{other.id!r} root {str(other.root)!r} on daemon {storage.daemon!r}"
                    )
        return problems

    def _route_problems(self) -> list[str]:
        known = {s.id for s in self.storages}
        problems = []
        for route in self.transfer.nominal_throughput.routes:
            match = ROUTE_RE.match(route)
            if match is None:
                problems.append(f"{route!r} is not a route (expected SOURCE->TARGET)")
                continue
            problems.extend(
                f"route {route!r} names unknown storage {endpoint!r}"
                for endpoint in (match["source"], match["target"])
                if endpoint not in known
            )
        return problems


def _duplicate_ids(kind: str, ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in ids:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return [f"duplicate {kind} id {value!r}" for value in duplicates]


def _is_inside(inner: Path, outer: Path) -> bool:
    return inner != outer and outer in inner.parents


@dataclass(frozen=True, slots=True)
class ConfigDocument:
    """A validated cluster config together with the text it came from.

    The controller serves the text rather than a re-serialisation: what a daemon
    validates is then exactly what the operator wrote, and the hash covers it.
    """

    config: ClusterConfig
    text: str

    @property
    def revision(self) -> int:
        return self.config.revision

    @property
    def content_hash(self) -> str:
        return self.config.content_hash


def parse_cluster_config(data: Mapping[str, Any], source: Path) -> ClusterConfig:
    try:
        return ClusterConfig.model_validate(dict(data))
    except ValidationError as exc:
        raise ConfigError(source, problems_from(exc)) from exc


def load_cluster_config(path: Path) -> ClusterConfig:
    return parse_cluster_config(read_yaml_mapping(path), path)


def load_cluster_document(path: Path) -> ConfigDocument:
    return ConfigDocument(config=load_cluster_config(path), text=path.read_text())


def parse_cluster_document(text: str, source: Path = Path("<served>")) -> ConfigDocument:
    """Validate a config a daemon was handed. It is never trusted for being ours."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(source, [f"not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(source, ["expected a mapping at the top level"])
    return ConfigDocument(config=parse_cluster_config(data, source), text=text)
