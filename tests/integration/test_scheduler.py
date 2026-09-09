"""The scheduler loop against a real database."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stashd.config.cluster import ClusterConfig
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import FairShareAccountRow, Fileset, StorageState, Transfer
from stashd.scheduler.loop import schedule_once

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


async def queued(
    session: AsyncSession,
    *,
    user: str = "mmustermann",
    name: str = "mydir",
    route: str = "HOT1->LOC2HOT",
    kind: TransferKind = TransferKind.WARM,
    state: TransferState = TransferState.SUBMITTED,
    bytes_total: int = 20 * GIB,
    submitted: datetime = T0,
) -> int:
    fileset = Fileset(
        name=name,
        owner_user=user,
        owner_uid=1000,
        owner_gid=1000,
        storage_id=route.split("->")[1],
        kind=FilesetKind.CACHED,
        state=FilesetState.POPULATING,
        path=f"/fake/cache/{user}/{name}",
        allocated_bytes=21 * GIB,
        source_storage_id=route.split("->")[0],
        source_path=f"/myuser/{name}",
        created_at=T0,
    )
    session.add(fileset)
    await session.flush()
    transfer = Transfer(
        kind=kind,
        user=user,
        fileset_id=fileset.id,
        peer_ref=f"{route.split('->')[0]}:/myuser/{name}",
        state=state,
        route=route,
        bytes_total=bytes_total,
        submitted_at=submitted,
    )
    session.add(transfer)
    await session.commit()
    return int(transfer.id)


async def run(
    sessions: async_sessionmaker[AsyncSession], cluster: ClusterConfig, dispatcher: Any
) -> list[int]:
    from tests.integration.conftest import FixedClock

    async with sessions() as session:
        planned = await schedule_once(
            session, cluster=cluster, dispatcher=dispatcher, clock=FixedClock(T0)
        )
    return [dispatch.transfer_id for dispatch in planned]


class TestDispatch:
    async def test_a_submitted_transfer_is_dispatched_and_assigned(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        transfer_id = await queued(session)

        assert await run(sessions, cluster_config, dispatcher) == [transfer_id]

        session.expire_all()
        stored = await session.get(Transfer, transfer_id)
        assert stored is not None and stored.state is TransferState.ASSIGNED
        assert stored.executing_daemon_id == "hot1"
        assert dispatcher.started[0]["transfer_id"] == transfer_id

    async def test_running_it_again_dispatches_nothing_new(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        await queued(session)
        await run(sessions, cluster_config, dispatcher)

        assert await run(sessions, cluster_config, dispatcher) == []
        assert len(dispatcher.started) == 1

    async def test_the_bandwidth_is_split_over_the_route(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        for name, user in (("one", "a"), ("two", "b"), ("three", "c")):
            await queued(session, name=name, user=user)

        await run(sessions, cluster_config, dispatcher)

        # 2Gbit over the route, capped at 800Mbit each: the cap holds until the third.
        limits = [call["bwlimit_bytes_per_s"] for call in dispatcher.started]
        assert limits == [100_000_000, 100_000_000, 250_000_000 // 3]

    async def test_the_per_user_limit_does_not_block_another_user(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        for index in range(3):
            await queued(session, name=f"mine{index}", user="mmustermann")
        theirs = await queued(session, name="theirs", user="jdoe")

        dispatched = await run(sessions, cluster_config, dispatcher)

        assert len(dispatched) == 3, "two of mine, and theirs"
        assert theirs in dispatched

    async def test_a_drained_storage_takes_no_new_work(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        await queued(session)
        session.add(StorageState(storage_id="LOC2HOT", drained=True))
        await session.commit()

        assert await run(sessions, cluster_config, dispatcher) == []

    async def test_a_release_needs_no_bandwidth(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        await queued(session, kind=TransferKind.RELEASE, route="LOC2HOT->LOC2HOT")

        await run(sessions, cluster_config, dispatcher)

        assert dispatcher.started == [], "a release moves nothing; the scheduler leaves it"

    async def test_a_transfer_already_running_is_counted_against_the_limits(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        for index in range(2):
            await queued(
                session, name=f"running{index}", state=TransferState.RUNNING, user="mmustermann"
            )
        await queued(session, name="waiting", user="mmustermann")

        assert await run(sessions, cluster_config, dispatcher) == []


class TestOrder:
    async def test_a_user_nobody_has_charged_yet_starts_from_nothing(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        newcomer = await queued(session, user="newcomer", name="first")

        assert await run(sessions, cluster_config, dispatcher) == [newcomer]

    async def test_the_user_who_has_moved_less_goes_first(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        session.add(FairShareAccountRow(user="heavy", points=500.0, decayed_at=T0))
        await session.commit()
        heavy = await queued(session, user="heavy", name="heavy", submitted=T0)
        light = await queued(
            session, user="light", name="light", submitted=T0 + timedelta(minutes=5)
        )

        dispatched = await run(sessions, cluster_config, dispatcher)

        assert dispatched.index(light) < dispatched.index(heavy)

    async def test_an_old_debt_has_decayed_by_the_time_it_is_read(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        long_ago = T0 - timedelta(days=70)
        session.add(FairShareAccountRow(user="heavy", points=500.0, decayed_at=long_ago))
        session.add(FairShareAccountRow(user="light", points=1.0, decayed_at=T0))
        await session.commit()
        heavy = await queued(session, user="heavy", name="heavy")
        light = await queued(session, user="light", name="light")

        dispatched = await run(sessions, cluster_config, dispatcher)

        assert dispatched.index(heavy) < dispatched.index(light), "ten half-lives"

    async def test_equal_standing_falls_back_to_who_asked_first(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        second = await queued(
            session, user="b", name="second", submitted=T0 + timedelta(minutes=1)
        )
        first = await queued(session, user="a", name="first", submitted=T0)

        assert await run(sessions, cluster_config, dispatcher) == [first, second]


class TestFailures:
    async def test_a_daemon_that_refuses_leaves_the_transfer_queued(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        from stashd.clients.filesets import DaemonUnavailable

        transfer_id = await queued(session)
        dispatcher.fail_start = DaemonUnavailable("hot1 is down")

        assert await run(sessions, cluster_config, dispatcher) == []

        session.expire_all()
        stored = await session.get(Transfer, transfer_id)
        assert stored is not None and stored.state is TransferState.SUBMITTED

    async def test_one_daemon_failing_does_not_stop_the_others(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        from stashd.clients.filesets import DaemonUnavailable

        first = await queued(session, name="first", user="a")
        await queued(session, name="second", user="b")
        dispatcher.fail_start_once = DaemonUnavailable("hot1 hiccupped")

        dispatched = await run(sessions, cluster_config, dispatcher)

        assert first not in dispatched
        assert len(dispatched) == 1


async def test_two_schedulers_cannot_dispatch_the_same_transfer(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    cluster_config: ClusterConfig,
    dispatcher: Any,
) -> None:
    """Only one holds the lock; the other finds nothing to do rather than doubling it."""
    import asyncio

    await queued(session)

    both = await asyncio.gather(
        run(sessions, cluster_config, dispatcher), run(sessions, cluster_config, dispatcher)
    )

    assert sorted(len(planned) for planned in both) == [0, 1]
    assert len(dispatcher.started) == 1


class TestControllerLoop:
    async def test_the_controller_schedules_beside_its_requests(
        self, api: Any, session: AsyncSession, dispatcher: Any
    ) -> None:
        """The same pass the background loop makes, driven once by hand."""
        from stashd.api.app import schedule

        await api.post(
            "/transfers",
            json={
                "kind": "warm",
                "source": {"storage": "HOT1", "path": "/myuser/mydirectory"},
                "target": {"storage": "LOC2HOT", "fileset": "mydir"},
            },
        )
        app = api._transport.app

        assert await schedule(app) == 1
        assert dispatcher.started[0]["transfer_id"] == 1

    async def test_a_process_with_nothing_to_schedule_does_nothing(
        self, storage_bootstrap: Any
    ) -> None:
        from stashd.api.app import create_app, schedule

        assert await schedule(create_app(storage_bootstrap)) == 0


class TestChannels:
    """Which channel a transfer uses is decided from the pair of daemons."""

    async def test_one_daemon_for_both_ends_means_a_local_path(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        valid_cluster: dict[str, Any],
        write_cluster: Any,
        dispatcher: Any,
    ) -> None:
        from stashd.config.cluster import load_cluster_config

        for storage in valid_cluster["storages"]:
            storage["daemon"] = "hot1"
        cluster = load_cluster_config(write_cluster(valid_cluster))
        await queued(session)

        await run(sessions, cluster, dispatcher)

        started = dispatcher.started[0]
        assert started["target_host"] is None
        assert started["target"] == "/fake/cache/mmustermann/mydir"

    async def test_two_daemons_mean_an_ssh_destination(
        self,
        session: AsyncSession,
        sessions: async_sessionmaker[AsyncSession],
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        """HOT1 is on hot1 and LOC2HOT on loc2hot, so the data crosses hosts."""
        await queued(session)

        await run(sessions, cluster_config, dispatcher)

        started = dispatcher.started[0]
        assert started["target_host"] == "stash-loc2.example.org"
        assert started["target_user"] == "mmustermann", "rsync connects as the owner"


async def flushing(session: AsyncSession) -> int:
    """An output fileset with a flush waiting to be dispatched."""
    fileset = Fileset(
        name="results",
        owner_user="mmustermann",
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.OUTPUT,
        state=FilesetState.FLUSHING,
        path="/fake/cache/mmustermann/results",
        allocated_bytes=4 * GIB,
        created_at=T0,
    )
    session.add(fileset)
    await session.flush()
    transfer = Transfer(
        kind=TransferKind.FLUSH,
        user="mmustermann",
        fileset_id=fileset.id,
        peer_ref="HOT1:/mmustermann/out",
        state=TransferState.SUBMITTED,
        route="LOC2HOT->HOT1",
        bytes_total=3 * GIB,
        submitted_at=T0,
    )
    session.add(transfer)
    await session.commit()
    return int(transfer.id)


class TestDispatchingAFlush:
    async def test_it_goes_out_from_the_fileset_to_the_target_path(
        self,
        sessions: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        cluster_config: ClusterConfig,
        dispatcher: Any,
    ) -> None:
        await flushing(session)

        await run(sessions, cluster_config, dispatcher)

        assert len(dispatcher.started) == 1
        started = dispatcher.started[0]
        assert started["storage_id"] == "LOC2HOT", "the daemon that holds the fileset"
        assert started["source_path"] == "/mmustermann/results", (
            "storage-relative: the executing daemon resolves it against its own root"
        )
        assert started["target"] == "/fake/source/mmustermann/out", (
            "resolved by the daemon that owns the target storage"
        )
        assert started["delete"] is False, "a flush never deletes at the target"

    async def test_a_flush_to_another_site_goes_over_ssh(
        self,
        sessions: async_sessionmaker[AsyncSession],
        session: AsyncSession,
        valid_cluster: dict[str, Any],
        write_cluster: Any,
        dispatcher: Any,
    ) -> None:
        """The fileset is on loc2hot and the target on hot1: the data crosses hosts."""
        from stashd.config.cluster import load_cluster_config

        for daemon in valid_cluster["daemons"]:
            if daemon["id"] == "hot1":
                daemon["host"] = "stash-loc1.example.org"
        cluster = load_cluster_config(write_cluster(valid_cluster))
        await flushing(session)

        await run(sessions, cluster, dispatcher)

        started = dispatcher.started[0]
        assert started["target_host"] == "stash-loc1.example.org"
        assert started["target_user"] == "mmustermann", "rsync connects as the owner"
