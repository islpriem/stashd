"""What a daemon told the controller about itself."""

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.errors import ErrorCode, NotFound, StashError
from stashd.models import Daemon


class Conflict(StashError):
    code = ErrorCode.CONFLICT


@dataclass(frozen=True, slots=True)
class Registration:
    daemon_id: str
    storages: list[str]
    config_revision: int
    version: str


async def register(
    session: AsyncSession, cluster: ClusterConfig, clock: Clock, announcement: Registration
) -> Daemon:
    """Accept what a daemon says, checked against the topology the controller authored."""
    try:
        cluster.daemon(announcement.daemon_id)
    except KeyError:
        raise NotFound(
            f"no daemon {announcement.daemon_id} in the cluster config",
            daemon=announcement.daemon_id,
        ) from None
    _check_storages(cluster, announcement)

    daemon = await session.get(Daemon, announcement.daemon_id)
    if daemon is None:
        daemon = Daemon(id=announcement.daemon_id, storages=[], config_revision=0, version="")
        session.add(daemon)
    daemon.storages = list(announcement.storages)
    daemon.config_revision = announcement.config_revision
    daemon.version = announcement.version
    daemon.last_seen_at = clock.now()
    await session.commit()
    return daemon


def _check_storages(cluster: ClusterConfig, announcement: Registration) -> None:
    for storage_id in announcement.storages:
        try:
            storage = cluster.storage(storage_id)
        except KeyError:
            raise NotFound(f"no storage {storage_id}", storage=storage_id) from None
        if storage.daemon != announcement.daemon_id:
            raise Conflict(
                f"{storage_id} is served by daemon {storage.daemon!r}, "
                f"not by {announcement.daemon_id!r}",
                storage=storage_id,
                daemon=storage.daemon,
            )


async def known_daemons(session: AsyncSession, ids: Sequence[str]) -> dict[str, Daemon]:
    if not ids:
        return {}
    rows = await session.scalars(sa.select(Daemon).where(Daemon.id.in_(list(ids))))
    return {daemon.id: daemon for daemon in rows}
