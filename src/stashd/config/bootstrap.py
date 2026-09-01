"""The bootstrap config: the local file one process reads to start.

Holds only what a process needs to identify itself and reach the controller. Topology and
policy live in the cluster config. Relative paths are resolved against the directory of
the config file, so a checkout can be run without absolute paths.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)
from pydantic.functional_validators import BeforeValidator

from stashd.config._yaml import problems_from, read_yaml_mapping
from stashd.config.errors import ConfigError


def _relative_to_config(value: Any, info: ValidationInfo) -> Path:
    path = Path(value)
    base = (info.context or {}).get("base_dir") if info.context else None
    return path if path.is_absolute() or base is None else Path(base) / path


ConfigRelativePath = Annotated[Path, BeforeValidator(_relative_to_config)]


class DaemonRole(StrEnum):
    CONTROLLER = "controller"
    STORAGE = "storage"


class IdentityKind(StrEnum):
    """How a storage daemon acts as the requesting user."""

    SUDO = "sudo"
    CURRENT = "current"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Node(Section):
    daemon_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    role: DaemonRole
    storages: list[str] = Field(default_factory=list)


class ControllerEndpoint(Section):
    url: str
    token_file: ConfigRelativePath


class Server(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8443, gt=0, lt=65536)
    tls_cert: ConfigRelativePath | None = None
    tls_key: ConfigRelativePath | None = None


class Broker(Section):
    url: str


class Database(Section):
    url: str


class Logging(Section):
    level: LogLevel = LogLevel.INFO
    format: LogFormat = LogFormat.JSON


class BootstrapConfig(Section):
    node: Node = Field(alias="self")
    cache_dir: ConfigRelativePath
    server: Server = Server()
    controller: ControllerEndpoint | None = None
    broker: Broker | None = None
    database: Database | None = None
    logging: Logging = Logging()
    # The controller authors this; a storage daemon keeps a local copy of what it fetched.
    cluster_config: ConfigRelativePath | None = None
    # The bearer token internal requests carry.
    peer_token_file: ConfigRelativePath | None = None
    identity: IdentityKind = IdentityKind.SUDO

    @property
    def is_controller(self) -> bool:
        return self.node.role is DaemonRole.CONTROLLER

    @model_validator(mode="after")
    def _check_role_requirements(self) -> Self:
        problems: list[str] = []
        if self.is_controller:
            if self.database is None:
                problems.append("a controller needs a database")
            if self.cluster_config is None:
                problems.append("a controller needs a cluster_config path")
        else:
            if self.controller is None:
                problems.append("a storage daemon needs a controller url and token_file")
            if self.broker is None:
                problems.append("a storage daemon needs a broker")
            if not self.node.storages:
                problems.append("a storage daemon needs at least one storage in self.storages")
        if (self.server.tls_cert is None) != (self.server.tls_key is None):
            problems.append("server: tls_cert and tls_key must be given together")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_bootstrap_config(path: Path) -> BootstrapConfig:
    data = read_yaml_mapping(path)
    try:
        return BootstrapConfig.model_validate(data, context={"base_dir": path.parent})
    except ValidationError as exc:
        raise ConfigError(path, problems_from(exc)) from exc
