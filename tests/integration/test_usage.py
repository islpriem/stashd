"""Usage reconciliation: what the filesystem says, against what the record claims."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.models import Fileset
from stashd.services.usage import reconcile_storage

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


async def fileset(
    session: AsyncSession,
    *,
    name: str = "results",
    allocated: int = 4 * GIB,
    used: int = 0,
    state: FilesetState = FilesetState.READY,
    user: str = "mmustermann",
) -> int:
    row = Fileset(
        name=name,
        owner_user=user,
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.OUTPUT,
        state=state,
        path=f"/fake/cache/{user}/{name}",
        allocated_bytes=allocated,
        used_bytes=used,
        created_at=T0,
    )
    session.add(row)
    await session.commit()
    return int(row.id)


class Measuring:
    """A daemon that answers with what it finds on disk."""

    def __init__(self, **found: tuple[int, int]) -> None:
        self.found = found
        self.asked: list[str] = []

    async def fileset_usage(self, storage_id: str, locations: list[Any]) -> dict[int, Any]:
        from stashd.clients.usage import Measured

        self.asked.append(storage_id)
        return {
            location.fileset_id: Measured(
                used_bytes=self.found.get(location.name, (0, 0))[0],
                file_count=self.found.get(location.name, (0, 0))[1],
            )
            for location in locations
        }


class TestReconciling:
    async def test_the_record_is_corrected_to_what_is_on_disk(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        fileset_id = await fileset(session, used=0)
        daemon = Measuring(results=(3 * GIB, 42))

        await reconcile_storage(session, storage_id="LOC2HOT", usage=daemon, clock=_clock())

        session.expire_all()
        row = await session.get(Fileset, fileset_id)
        assert row is not None
        assert row.used_bytes == 3 * GIB
        assert row.file_count == 42
        assert row.used_bytes_at == T0

    async def test_usage_past_the_allocation_flags_the_fileset(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        fileset_id = await fileset(session, allocated=GIB)
        daemon = Measuring(results=(2 * GIB, 1))

        await reconcile_storage(session, storage_id="LOC2HOT", usage=daemon, clock=_clock())

        session.expire_all()
        row = await session.get(Fileset, fileset_id)
        assert row is not None and row.over_allocation is True

    async def test_a_fileset_back_within_its_allocation_is_cleared(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        fileset_id = await fileset(session, allocated=4 * GIB)
        row = await session.get(Fileset, fileset_id)
        assert row is not None
        row.over_allocation = True
        await session.commit()

        await reconcile_storage(
            session, storage_id="LOC2HOT", usage=Measuring(results=(GIB, 1)), clock=_clock()
        )

        session.expire_all()
        row = await session.get(Fileset, fileset_id)
        assert row is not None and row.over_allocation is False

    async def test_released_filesets_are_not_measured(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        await fileset(session, state=FilesetState.RELEASED, name="gone")
        daemon = Measuring()

        measured = await reconcile_storage(
            session, storage_id="LOC2HOT", usage=daemon, clock=_clock()
        )

        assert measured == 0

    async def test_a_storage_with_no_filesets_asks_no_daemon(
        self, session: AsyncSession, cluster_config: ClusterConfig
    ) -> None:
        daemon = Measuring()

        await reconcile_storage(session, storage_id="LOC2HOT", usage=daemon, clock=_clock())

        assert daemon.asked == []

    async def test_an_offender_blocks_the_users_next_allocation(
        self, session: AsyncSession, cluster_config: ClusterConfig, api: Any
    ) -> None:
        await fileset(session, allocated=GIB, name="over")
        await reconcile_storage(
            session, storage_id="LOC2HOT", usage=Measuring(over=(2 * GIB, 1)), clock=_clock()
        )

        response = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "next", "size_bytes": GIB}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "OVER_ALLOCATION"


def _clock() -> Any:
    from tests.integration.conftest import FixedClock

    return FixedClock(T0)


class TestTheControllerLoop:
    async def test_every_cache_storage_is_reconciled(
        self, sessions: Any, cluster_config: ClusterConfig
    ) -> None:
        from stashd.api.app import reconcile_usage

        async with sessions() as session:
            await fileset(session, name="one")
        daemon = Measuring(one=(GIB, 3))
        app = _app_with(sessions, cluster_config, daemon)

        measured = await reconcile_usage(app, "LOC2HOT")

        assert measured == 1
        assert daemon.asked == ["LOC2HOT"]

    async def test_a_daemon_that_cannot_answer_does_not_stop_the_controller(
        self, sessions: Any, cluster_config: ClusterConfig
    ) -> None:
        from stashd.api.app import reconcile_usage
        from stashd.clients.filesets import DaemonUnavailable

        async with sessions() as session:
            await fileset(session, name="one")

        class Unreachable:
            async def fileset_usage(self, storage_id: str, locations: list[Any]) -> Any:
                raise DaemonUnavailable("loc2hot is down", daemon="loc2hot")

        app = _app_with(sessions, cluster_config, Unreachable())

        assert await reconcile_usage(app, "LOC2HOT") == 0


def _app_with(sessions: Any, cluster: ClusterConfig, usage: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        state=SimpleNamespace(sessions=sessions, cluster=cluster, usage=usage, clock=_clock())
    )
