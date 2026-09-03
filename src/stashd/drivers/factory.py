"""Which driver serves which storage on this daemon.

A storage daemon refuses to start when a storage it declares is absent from the cluster
config, is assigned to another daemon, or has a root that is not an existing directory.
"""

from collections.abc import Sequence
from pathlib import Path

from stashd.config.cluster import ClusterConfig, Driver, Storage
from stashd.config.errors import ConfigError
from stashd.drivers.base import StorageCapabilities, StorageDriver
from stashd.drivers.posix import PosixDriver
from stashd.identity.base import Identity

_CAPABILITIES: dict[Driver, StorageCapabilities] = {Driver.POSIX: PosixDriver.capabilities}


def capabilities_for(driver: Driver) -> StorageCapabilities:
    """What a storage can do, without touching it: the controller has no roots."""
    return _CAPABILITIES[driver]


def build_drivers(
    cluster: ClusterConfig,
    storage_ids: Sequence[str],
    identity: Identity,
    *,
    daemon_id: str | None = None,
    config_path: Path | None = None,
) -> dict[str, StorageDriver]:
    drivers: dict[str, StorageDriver] = {}
    problems: list[str] = []
    for storage_id in storage_ids:
        try:
            storage = cluster.storage(storage_id)
        except KeyError:
            problems.append(f"storage {storage_id!r} is not in the cluster config")
            continue
        if daemon_id is not None and storage.daemon != daemon_id:
            problems.append(
                f"storage {storage_id!r} is served by daemon {storage.daemon!r}, "
                f"not by {daemon_id!r}"
            )
            continue
        problem = _root_problem(storage)
        if problem is not None:
            problems.append(problem)
            continue
        drivers[storage_id] = _driver_for(storage, identity)
    if problems:
        raise ConfigError(config_path or Path("cluster.yaml"), problems)
    return drivers


def _root_problem(storage: Storage) -> str | None:
    if not storage.root.exists():
        return f"root {str(storage.root)!r} of storage {storage.id!r} does not exist"
    if not storage.root.is_dir():
        return f"root {str(storage.root)!r} of storage {storage.id!r} is not a directory"
    return None


def _driver_for(storage: Storage, identity: Identity) -> StorageDriver:
    if storage.driver is Driver.POSIX:
        return PosixDriver(
            storage_id=storage.id,
            fileset_prefix=storage.fileset_prefix,
            identity=identity,
            fileset_mode=storage.fileset_mode,
            root=storage.root,
        )
    raise ConfigError(  # pragma: no cover - the enum has one member today
        Path("cluster.yaml"), [f"storage {storage.id!r}: no driver named {storage.driver!r}"]
    )
