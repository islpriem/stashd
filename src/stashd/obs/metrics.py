"""Prometheus metrics, built from the database on each scrape.

Everything here is a snapshot: the controller's numbers live in PostgreSQL, so a
scrape reads them rather than keeping counters in a process that may restart. Duration
and queue wait are summaries — a sum and a count, no quantiles — because the
distribution is not carried through retention pruning.
"""

from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.filesets import FilesetState
from stashd.domain.transfers import TransferState
from stashd.models import Daemon, Fileset, Transfer, TransferStat

WAITING = (TransferState.SUBMITTED, TransferState.ASSIGNED, TransferState.RUNNING)
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


async def render(
    session: AsyncSession, *, cluster: ClusterConfig, clock: Clock, unreachable_after: timedelta
) -> bytes:
    registry = CollectorRegistry()
    # Gauges, not Counters: every family is recomputed from the database per scrape, so
    # a counter's _created timestamp would say nothing.
    transfers = Gauge(
        "stash_transfers_total",
        "Transfers by kind, state and route.",
        ["kind", "state", "route"],
        registry=registry,
    )
    moved = Gauge(
        "stash_transfer_bytes_total",
        "Bytes transferred, by route.",
        ["route"],
        registry=registry,
    )
    duration = _summary(registry, "stash_transfer_duration_seconds", "Time spent running.")
    waited = _summary(registry, "stash_queue_wait_seconds", "Time spent waiting to start.")

    depth = Gauge(
        "stash_queue_depth",
        "Transfers waiting or running that touch this storage.",
        ["storage"],
        registry=registry,
    )
    allocated = Gauge(
        "stash_storage_allocated_bytes", "Reserved bytes.", ["storage"], registry=registry
    )
    used = Gauge("stash_storage_used_bytes", "Bytes held.", ["storage"], registry=registry)
    filesets = Gauge(
        "stash_filesets_total",
        "Filesets by storage, kind and state.",
        ["storage", "kind", "state"],
        registry=registry,
    )
    up = Gauge(
        "stash_daemon_up", "Whether a daemon was seen recently.", ["daemon"], registry=registry
    )
    revision = Gauge(
        "stash_config_revision",
        "The config revision a daemon runs.",
        ["daemon"],
        registry=registry,
    )

    for storage in cluster.storages:
        allocated.labels(storage=storage.id).set(0)
        used.labels(storage=storage.id).set(0)
        depth.labels(storage=storage.id).set(0)

    await _transfers(session, transfers, moved, duration, waited, depth)
    await _filesets(session, filesets, allocated, used)
    await _daemons(session, up, revision, clock, unreachable_after)
    rendered: bytes = generate_latest(registry)
    return rendered


@dataclass(frozen=True, slots=True)
class Summary:
    """A sum and a count and no quantiles: pruning keeps totals, not distributions."""

    total: Gauge
    count: Gauge

    def observed(self, seconds: float, times: int) -> None:
        self.total.set(seconds)
        self.count.set(times)


def _summary(registry: CollectorRegistry, name: str, help_text: str) -> Summary:
    return Summary(
        total=Gauge(f"{name}_sum", help_text, registry=registry),
        count=Gauge(f"{name}_count", f"{help_text} Observations.", registry=registry),
    )


async def _transfers(
    session: AsyncSession,
    transfers: Gauge,
    moved: Gauge,
    duration: "Summary",
    waited: "Summary",
    depth: Gauge,
) -> None:
    counted = await session.execute(
        sa.select(Transfer.kind, Transfer.state, Transfer.route, sa.func.count()).group_by(
            Transfer.kind, Transfer.state, Transfer.route
        )
    )
    for kind, state, route, count in counted:
        transfers.labels(kind=str(kind), state=str(state), route=route).inc(count)

    for route, total in await session.execute(
        sa.select(Transfer.route, sa.func.sum(Transfer.bytes_done)).group_by(Transfer.route)
    ):
        moved.labels(route=route).inc(int(total or 0))
    for route, total in await session.execute(
        sa.select(TransferStat.route, sa.func.sum(TransferStat.bytes_transferred)).group_by(
            TransferStat.route
        )
    ):
        moved.labels(route=route).inc(int(total or 0))

    ran, wait, finished, started = 0.0, 0.0, 0, 0
    for transfer in await session.scalars(sa.select(Transfer)):
        if transfer.started_at is not None:
            wait += (transfer.started_at - transfer.submitted_at).total_seconds()
            started += 1
            if transfer.finished_at is not None:
                ran += (transfer.finished_at - transfer.started_at).total_seconds()
                finished += 1
    for stat in await session.scalars(sa.select(TransferStat)):
        wait += stat.queue_wait_seconds
        ran += stat.running_seconds
        started += stat.transfers
        finished += stat.transfers
    duration.observed(ran, finished)
    waited.observed(wait, started)

    for route, count in await session.execute(
        sa.select(Transfer.route, sa.func.count())
        .where(Transfer.state.in_(WAITING))
        .group_by(Transfer.route)
    ):
        for storage_id in str(route).split("->"):
            depth.labels(storage=storage_id).inc(count)


async def _filesets(
    session: AsyncSession, filesets: Gauge, allocated: Gauge, used: Gauge
) -> None:
    for storage_id, kind, state, count in await session.execute(
        sa.select(Fileset.storage_id, Fileset.kind, Fileset.state, sa.func.count()).group_by(
            Fileset.storage_id, Fileset.kind, Fileset.state
        )
    ):
        filesets.labels(storage=storage_id, kind=str(kind), state=str(state)).set(count)

    for storage_id, reserved, held in await session.execute(
        sa.select(
            Fileset.storage_id,
            sa.func.sum(Fileset.allocated_bytes),
            sa.func.sum(Fileset.used_bytes),
        )
        .where(Fileset.state != FilesetState.RELEASED)
        .group_by(Fileset.storage_id)
    ):
        allocated.labels(storage=storage_id).set(int(reserved or 0))
        used.labels(storage=storage_id).set(int(held or 0))


async def _daemons(
    session: AsyncSession,
    up: Gauge,
    revision: Gauge,
    clock: Clock,
    unreachable_after: timedelta,
) -> None:
    cutoff = clock.now() - unreachable_after
    for daemon in await session.scalars(sa.select(Daemon)):
        up.labels(daemon=daemon.id).set(1.0 if daemon.last_seen_at >= cutoff else 0.0)
        revision.labels(daemon=daemon.id).set(daemon.config_revision)
