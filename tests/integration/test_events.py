"""Daemon events reaching the controller."""

from datetime import UTC, datetime

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def warming(session: AsyncSession) -> tuple[int, int]:
    """A cached fileset being filled by a running warm."""
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
        warm_started_at=T0,
    )
    session.add(fileset)
    await session.flush()
    transfer = Transfer(
        kind=TransferKind.WARM,
        user="mmustermann",
        fileset_id=fileset.id,
        state=TransferState.ASSIGNED,
        route="HOT1->LOC2HOT",
        bytes_total=20 * GIB,
        submitted_at=T0,
        executing_daemon_id="hot1",
    )
    session.add(transfer)
    await session.commit()
    return transfer.id, fileset.id


def event(transfer_id: int, sequence: int, kind: str, **extra: object) -> dict[str, object]:
    return {
        "transfer_id": transfer_id,
        "sequence": sequence,
        "kind": kind,
        "daemon_id": "hot1",
        "at": "2026-09-01T12:05:00Z",
        **extra,
    }


class TestApplying:
    async def test_started_then_progress_then_finished(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, fileset_id = await warming(session)

        assert (
            await peer.post("/events", json=event(transfer_id, 1, "started"))
        ).status_code == 200
        await peer.post("/events", json=event(transfer_id, 2, "progress", bytes_done=10 * GIB))
        await peer.post(
            "/events",
            json=event(transfer_id, 3, "finished", bytes_done=20 * GIB, files_done=12043),
        )

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        fileset = await session.get(Fileset, fileset_id)
        assert transfer is not None and fileset is not None
        assert transfer.state is TransferState.SUCCEEDED
        assert transfer.bytes_done == 20 * GIB
        assert transfer.files_done == 12043
        assert transfer.finished_at is not None
        assert fileset.state is FilesetState.READY
        assert fileset.warm_finished_at is not None
        assert fileset.used_bytes == 20 * GIB

    async def test_a_failure_leaves_the_fileset_failed_and_the_reservation_held(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, fileset_id = await warming(session)
        await peer.post("/events", json=event(transfer_id, 1, "started"))

        await peer.post(
            "/events",
            json=event(transfer_id, 2, "failed", failure="permission_denied", message="denied"),
        )

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        fileset = await session.get(Fileset, fileset_id)
        assert transfer is not None and fileset is not None
        assert transfer.state is TransferState.FAILED
        assert transfer.error_code == "permission_denied"
        assert fileset.state is FilesetState.FAILED
        assert fileset.allocated_bytes == 21 * GIB

    async def test_a_repeated_event_changes_nothing(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await warming(session)
        await peer.post("/events", json=event(transfer_id, 1, "started"))
        await peer.post("/events", json=event(transfer_id, 2, "progress", bytes_done=10 * GIB))

        again = await peer.post(
            "/events", json=event(transfer_id, 2, "progress", bytes_done=10 * GIB)
        )

        assert again.status_code == 200
        assert again.json()["applied"] is False
        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None and transfer.bytes_done == 10 * GIB

    async def test_an_event_that_arrives_late_never_moves_the_state_back(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await warming(session)
        await peer.post("/events", json=event(transfer_id, 1, "started"))
        await peer.post("/events", json=event(transfer_id, 5, "finished", bytes_done=20 * GIB))

        late = await peer.post(
            "/events", json=event(transfer_id, 3, "progress", bytes_done=5 * GIB)
        )

        assert late.json()["applied"] is False
        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None
        assert transfer.state is TransferState.SUCCEEDED
        assert transfer.bytes_done == 20 * GIB

    async def test_progress_never_exceeds_the_total(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        transfer_id, _ = await warming(session)
        await peer.post("/events", json=event(transfer_id, 1, "started"))

        await peer.post("/events", json=event(transfer_id, 2, "progress", bytes_done=99 * GIB))

        session.expire_all()
        transfer = await session.get(Transfer, transfer_id)
        assert transfer is not None
        assert transfer.bytes_done == transfer.bytes_total

    async def test_an_event_for_a_transfer_that_does_not_exist_is_not_found(
        self, peer: httpx2.AsyncClient
    ) -> None:
        assert (await peer.post("/events", json=event(4711, 1, "started"))).status_code == 404

    async def test_events_need_the_peer_token(self, api: httpx2.AsyncClient) -> None:
        """A MUNGE credential does not open the internal API."""
        response = await api.post(
            "http://controller/internal/v1/events", json=event(1, 1, "started")
        )

        assert response.status_code == 401
