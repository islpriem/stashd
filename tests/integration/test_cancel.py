"""Cancelling a transfer."""

from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import AuditEvent, Fileset, Transfer

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


async def in_flight(
    session: AsyncSession,
    *,
    state: TransferState = TransferState.RUNNING,
    user: str = "mmustermann",
    task_id: str | None = "task-1",
) -> tuple[int, int]:
    fileset = Fileset(
        name="mydir",
        owner_user=user,
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
        user=user,
        fileset_id=fileset.id,
        peer_ref="HOT1:/myuser/mydir",
        state=state,
        route="HOT1->LOC2HOT",
        bytes_total=20 * GIB,
        bytes_done=5 * GIB if state is TransferState.RUNNING else 0,
        submitted_at=T0,
        started_at=T0 if state is TransferState.RUNNING else None,
        executing_daemon_id="hot1" if task_id else None,
        task_id=task_id,
    )
    session.add(transfer)
    await session.commit()
    return int(transfer.id), int(fileset.id)


class TestCancelling:
    async def test_a_queued_transfer_is_cancelled_at_once(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        transfer_id, fileset_id = await in_flight(
            session, state=TransferState.SUBMITTED, task_id=None
        )

        response = await api.delete(f"/transfers/{transfer_id}")

        assert response.status_code == 200
        assert response.json()["state"] == "CANCELLED"
        assert not dispatcher.aborted, "no daemon had it yet"
        session.expire_all()
        fileset = await session.get(Fileset, fileset_id)
        assert fileset is not None and fileset.state is FilesetState.FAILED

    async def test_a_running_transfer_is_stopped_on_its_daemon(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        transfer_id, fileset_id = await in_flight(session)

        response = await api.delete(f"/transfers/{transfer_id}")

        assert response.status_code == 200
        assert dispatcher.aborted == [("HOT1", "task-1")]
        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        fileset = await session.get(Fileset, fileset_id)
        assert transfer is not None and fileset is not None
        assert transfer.state is TransferState.CANCELLED
        assert transfer.finished_at is not None
        assert fileset.state is FilesetState.FAILED, (
            "the partial data is the owner's to release"
        )
        assert fileset.allocated_bytes == 21 * GIB, "and it still holds its reservation"

    async def test_a_daemon_that_cannot_be_reached_still_cancels(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        """The controller must not be left believing a transfer is running."""
        from stashd.clients.filesets import DaemonUnavailable

        transfer_id, _ = await in_flight(session)
        dispatcher.fail_abort = DaemonUnavailable("hot1 is down")

        response = await api.delete(f"/transfers/{transfer_id}")

        assert response.status_code == 200
        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.state is TransferState.CANCELLED

    async def test_cancelling_is_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await in_flight(session)

        await api.delete(f"/transfers/{transfer_id}")

        event = (await session.scalars(sa.select(AuditEvent))).one()
        assert (event.action, event.object_id) == ("cancel", str(transfer_id))


class TestRefusals:
    async def test_a_finished_transfer_cannot_be_cancelled(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await in_flight(session, state=TransferState.SUCCEEDED, task_id=None)

        response = await api.delete(f"/transfers/{transfer_id}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_someone_elses_transfer_is_forbidden(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await in_flight(session, user="jdoe")

        response = await api.delete(f"/transfers/{transfer_id}")

        assert response.status_code == 403

    async def test_an_admin_may_cancel_anyones_transfer(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await in_flight(session, user="jdoe")

        response = await api.delete(
            f"/transfers/{transfer_id}", headers={"Authorization": "Munge cred-admin"}
        )

        assert response.status_code == 200

    async def test_an_unknown_transfer_is_not_found(self, api: httpx2.AsyncClient) -> None:
        assert (await api.delete("/transfers/4711")).status_code == 404

    async def test_a_cancelled_transfer_gets_its_points_back(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        from stashd.models import FairShareAccountRow

        session.add(FairShareAccountRow(user="mmustermann", points=20.0, decayed_at=T0))
        await session.commit()
        transfer_id, _ = await in_flight(session, state=TransferState.SUBMITTED, task_id=None)

        await api.delete(f"/transfers/{transfer_id}")

        session.expire_all()
        account = await session.get(FairShareAccountRow, "mmustermann")
        assert account is not None and account.points == 0.0
