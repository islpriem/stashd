"""Pruning old rows, keeping the numbers that reports are built from."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import AuditEvent, Fileset, Transfer, TransferStat
from stashd.services.retention import prune

pytestmark = pytest.mark.integration

GIB = 1024**3
ADMIN = {"Authorization": "Munge cred-admin"}
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def finished(
    session: AsyncSession,
    *,
    age: timedelta,
    state: TransferState = TransferState.SUCCEEDED,
    bytes_done: int = 10 * GIB,
    user: str = "mmustermann",
) -> int:
    submitted = NOW - age
    fileset = Fileset(
        name=f"fs{age.days}{state}{user}",
        owner_user=user,
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.CACHED,
        state=FilesetState.READY,
        path="/fake/cache/x",
        allocated_bytes=20 * GIB,
        created_at=submitted,
    )
    session.add(fileset)
    await session.flush()
    transfer = Transfer(
        kind=TransferKind.WARM,
        user=user,
        fileset_id=fileset.id,
        state=state,
        route="HOT1->LOC2HOT",
        bytes_total=bytes_done,
        bytes_done=bytes_done,
        submitted_at=submitted,
        started_at=submitted + timedelta(minutes=1),
        finished_at=submitted + timedelta(minutes=11),
    )
    session.add(transfer)
    await session.commit()
    return int(transfer.id)


def _clock() -> Any:
    from tests.integration.conftest import FixedClock

    return FixedClock(NOW)


class TestPruning:
    async def test_a_transfer_past_its_retention_is_removed(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        old = await finished(session, age=timedelta(days=120))
        recent = await finished(session, age=timedelta(days=1))

        await prune(session, cluster=cluster_config, clock=_clock())

        left = list(await session.scalars(sa.select(Transfer.id)))
        assert left == [recent]
        assert old not in left

    async def test_what_is_still_running_is_never_pruned(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        running = await finished(session, age=timedelta(days=200), state=TransferState.RUNNING)

        await prune(session, cluster=cluster_config, clock=_clock())

        assert list(await session.scalars(sa.select(Transfer.id))) == [running]

    async def test_the_numbers_survive_the_rows(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await finished(session, age=timedelta(days=120))
        await finished(session, age=timedelta(days=120), state=TransferState.FAILED)

        await prune(session, cluster=cluster_config, clock=_clock())

        stats = list(await session.scalars(sa.select(TransferStat)))
        assert len(stats) == 1
        assert stats[0].transfers == 2
        assert stats[0].succeeded == 1
        assert stats[0].bytes_transferred == 20 * GIB
        assert stats[0].user == "mmustermann"
        assert stats[0].route == "HOT1->LOC2HOT"

    async def test_pruning_twice_adds_to_what_was_already_rolled_up(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await finished(session, age=timedelta(days=120))
        await prune(session, cluster=cluster_config, clock=_clock())
        await finished(session, age=timedelta(days=120), state=TransferState.FAILED)

        await prune(session, cluster=cluster_config, clock=_clock())

        stats = list(await session.scalars(sa.select(TransferStat)))
        assert len(stats) == 1, "one row per day, user, storage, route and kind"
        assert stats[0].transfers == 2

    async def test_audit_events_are_pruned_on_their_own_retention(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        session.add(
            AuditEvent(
                ts=NOW - timedelta(days=400),
                actor_uid=1000,
                subject_user="mmustermann",
                object_type="fileset",
                action="create",
                result="OK",
            )
        )
        session.add(
            AuditEvent(
                ts=NOW - timedelta(days=10),
                actor_uid=1000,
                subject_user="mmustermann",
                object_type="fileset",
                action="create",
                result="OK",
            )
        )
        await session.commit()

        await prune(session, cluster=cluster_config, clock=_clock())

        left = list(await session.scalars(sa.select(AuditEvent.ts)))
        assert len(left) == 1


class TestReportsAfterPruning:
    async def test_the_usage_report_still_counts_what_was_pruned(
        self, api: httpx2.AsyncClient, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await finished(session, age=timedelta(days=120))
        await finished(session, age=timedelta(days=1))

        await prune(session, cluster=cluster_config, clock=_clock())

        row = (await api.get("/reports/usage", headers=ADMIN)).json()["groups"][0]
        assert row["transfers"] == 2
        assert row["bytes_transferred"] == 20 * GIB

    async def test_a_pruned_window_still_groups_by_route(
        self, api: httpx2.AsyncClient, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await finished(session, age=timedelta(days=120))
        await prune(session, cluster=cluster_config, clock=_clock())

        body = (
            await api.get("/reports/usage", params={"group_by": "route"}, headers=ADMIN)
        ).json()

        assert [row["key"] for row in body["groups"]] == ["HOT1->LOC2HOT"]

    async def test_the_window_still_excludes_what_falls_outside_it(
        self, api: httpx2.AsyncClient, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await finished(session, age=timedelta(days=120))
        await prune(session, cluster=cluster_config, clock=_clock())

        body = (
            await api.get(
                "/reports/usage",
                params={"since": (NOW - timedelta(days=30)).isoformat()},
                headers=ADMIN,
            )
        ).json()

        assert body["groups"] == []


class TestTheControllerLoop:
    async def test_the_controller_prunes_without_being_asked(
        self, sessions: Any, cluster_config: ClusterConfig, session: AsyncSession
    ) -> None:
        from types import SimpleNamespace

        from stashd.api.app import prune_old_rows

        await finished(session, age=timedelta(days=120))
        app = SimpleNamespace(
            state=SimpleNamespace(sessions=sessions, cluster=cluster_config, clock=_clock())
        )

        assert await prune_old_rows(app) == 1
        assert list(await session.scalars(sa.select(Transfer.id))) == []
