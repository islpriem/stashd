"""What the controller believes after a restart is what the daemons say."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.config.cluster import ClusterConfig
from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer
from stashd.services.reconcile import reconcile

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


async def in_flight(
    session: AsyncSession,
    *,
    state: TransferState = TransferState.RUNNING,
    task_id: str | None = "task-1",
    started: datetime | None = T0,
    submitted: datetime = T0,
) -> tuple[int, int]:
    fileset = Fileset(
        name="mydir",
        owner_user="mmustermann",
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.CACHED,
        state=FilesetState.POPULATING,
        path="/fake/cache/mmustermann/mydir",
        allocated_bytes=21 * GIB,
        source_storage_id="HOT1",
        source_path="/myuser/mydir",
        created_at=T0,
    )
    session.add(fileset)
    await session.flush()
    transfer = Transfer(
        kind=TransferKind.WARM,
        user="mmustermann",
        fileset_id=fileset.id,
        peer_ref="HOT1:/myuser/mydir",
        state=state,
        route="HOT1->LOC2HOT",
        bytes_total=20 * GIB,
        submitted_at=submitted,
        started_at=started,
        executing_daemon_id="hot1",
        task_id=task_id,
    )
    session.add(transfer)
    await session.commit()
    return int(transfer.id), int(fileset.id)


async def run(
    session: AsyncSession, cluster: ClusterConfig, dispatcher: Any, now: datetime = T0
) -> int:
    from tests.integration.conftest import FixedClock

    return await reconcile(
        session, cluster=cluster, dispatcher=dispatcher, clock=FixedClock(now)
    )


class TestAdopting:
    async def test_a_transfer_the_daemon_finished_is_adopted(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        transfer_id, fileset_id = await in_flight(session)
        dispatcher.task_states["task-1"] = {
            "state": "SUCCEEDED",
            "bytes_done": 20 * GIB,
            "files_done": 12043,
        }

        assert await run(session, cluster_config, dispatcher) == 1

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        fileset = await session.get(Fileset, fileset_id)
        assert transfer is not None and fileset is not None
        assert transfer.state is TransferState.SUCCEEDED
        assert transfer.bytes_done == 20 * GIB
        assert fileset.state is FilesetState.READY

    async def test_a_transfer_the_daemon_failed_is_adopted(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        transfer_id, _ = await in_flight(session)
        dispatcher.task_states["task-1"] = {"state": "FAILED", "failure": "no_space"}

        await run(session, cluster_config, dispatcher)

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None
        assert transfer.state is TransferState.FAILED
        assert transfer.error_code == "no_space"

    async def test_a_transfer_still_running_is_left_alone(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        transfer_id, _ = await in_flight(session)
        dispatcher.task_states["task-1"] = {"state": "RUNNING", "bytes_done": 5 * GIB}

        assert await run(session, cluster_config, dispatcher) == 0

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.state is TransferState.RUNNING


class TestUnreachable:
    async def test_a_daemon_that_cannot_be_asked_yet_keeps_its_transfer(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        """Within the grace period the daemon may simply be restarting."""
        transfer_id, _ = await in_flight(session)
        dispatcher.fail_task_state = True

        assert (
            await run(session, cluster_config, dispatcher, now=T0 + timedelta(minutes=1)) == 0
        )

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.state is TransferState.RUNNING

    async def test_a_daemon_unreachable_for_too_long_fails_its_transfers(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        transfer_id, fileset_id = await in_flight(session)
        dispatcher.fail_task_state = True

        assert await run(session, cluster_config, dispatcher, now=T0 + timedelta(hours=1)) == 1

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        fileset = await session.get(Fileset, fileset_id)
        assert transfer is not None and fileset is not None
        assert transfer.state is TransferState.FAILED
        assert transfer.error_code == "daemon_unreachable"
        assert fileset.state is FilesetState.FAILED, "its owner decides what happens next"

    async def test_a_transfer_the_daemon_never_heard_of_is_failed(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        transfer_id, _ = await in_flight(session)

        await run(session, cluster_config, dispatcher, now=T0 + timedelta(hours=1))

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.state is TransferState.FAILED

    async def test_a_transfer_that_was_assigned_but_never_started_is_offered_again(
        self, session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
    ) -> None:
        """It never reached a daemon, so it can simply be queued again."""
        transfer_id, _ = await in_flight(
            session, state=TransferState.ASSIGNED, task_id=None, started=None
        )

        assert await run(session, cluster_config, dispatcher) == 1

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.state is TransferState.SUBMITTED


async def test_nothing_in_flight_is_no_work(
    session: AsyncSession, cluster_config: ClusterConfig, dispatcher: Any
) -> None:
    assert await run(session, cluster_config, dispatcher) == 0


async def test_a_controller_reconciles_when_it_starts(
    api: Any, session: AsyncSession, dispatcher: Any
) -> None:
    """The lifespan does this; here it is driven once by hand."""
    from stashd.api.app import reconcile_transfers

    transfer_id, _ = await in_flight(session)
    dispatcher.task_states["task-1"] = {"state": "SUCCEEDED", "bytes_done": 20 * GIB}

    assert await reconcile_transfers(api._transport.app) == 1

    session.expire_all()
    transfer = await session.get(Transfer, transfer_id)
    assert transfer is not None and transfer.state is TransferState.SUCCEEDED


async def test_a_process_with_no_daemons_to_ask_does_nothing(storage_bootstrap: Any) -> None:
    from stashd.api.app import create_app, reconcile_transfers

    assert await reconcile_transfers(create_app(storage_bootstrap)) == 0
