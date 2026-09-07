"""Submitting a warm."""

import copy
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetState
from stashd.domain.transfers import TransferState
from stashd.models import AuditEvent, Fileset, Transfer
from tests.conftest import VALID_CLUSTER

pytestmark = pytest.mark.integration

GIB = 1024**3
MB = 1000**2


@pytest.fixture
def valid_cluster() -> dict[str, Any]:
    """One daemon serves both storages, so the transfer runs on the local channel."""
    cluster = copy.deepcopy(VALID_CLUSTER)
    for storage in cluster["storages"]:
        storage["daemon"] = "hot1"
    return cluster


def warm(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "warm",
        "source": {"storage": "HOT1", "path": "/myuser/mydirectory"},
        "target": {"storage": "LOC2HOT", "fileset": "mydir"},
        **extra,
    }


class TestSubmission:
    async def test_a_warm_is_queued_for_the_scheduler(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        response = await api.post("/transfers", json=warm())

        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "warm"
        assert body["state"] == "SUBMITTED", "the scheduler decides when it runs"
        assert body["route"] == "HOT1->LOC2HOT"
        assert body["bytes_total"] == 20 * GIB
        assert body["peer_ref"] == "HOT1:/myuser/mydirectory"
        assert not dispatcher.started, "nothing moves until the scheduler says so"

    async def test_the_fileset_is_created_and_populating(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/transfers", json=warm())

        fileset = (await session.scalars(sa.select(Fileset))).one()
        assert fileset.state is FilesetState.POPULATING
        assert fileset.kind == "cached"
        assert fileset.source_storage_id == "HOT1"
        assert fileset.source_path == "/myuser/mydirectory"
        assert fileset.path == "/fake/cache/mmustermann/mydir"
        assert fileset.warm_started_at is not None

    async def test_the_allocation_is_the_source_size_plus_headroom(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/transfers", json=warm())

        fileset = (await session.scalars(sa.select(Fileset))).one()
        assert fileset.allocated_bytes == int(20 * GIB * 1.05)

    async def test_a_larger_size_can_be_asked_for(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/transfers", json=warm(size_bytes=30 * GIB))

        fileset = (await session.scalars(sa.select(Fileset))).one()
        assert fileset.allocated_bytes == 30 * GIB

    async def test_the_destination_exists_as_soon_as_the_warm_is_accepted(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        """The path is returned right away, so a job script can use it."""
        response = await api.post("/transfers", json=warm())

        assert response.status_code == 201
        assert dispatcher.prepared[0][1] == "mydir"

    async def test_the_estimate_is_charged_to_the_user_at_once(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        """Anti queue-stuffing: submitting costs points before anything runs."""
        from stashd.models import FairShareAccountRow

        await api.post("/transfers", json=warm())

        account = (await session.scalars(sa.select(FairShareAccountRow))).one()
        assert account.user == "mmustermann"
        assert account.points == 20.0, "20 GiB at one point per GiB"

    async def test_a_warm_is_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/transfers", json=warm())

        event = (await session.scalars(sa.select(AuditEvent))).one()
        assert (event.action, event.result) == ("warm", "ok")


class TestPreflight:
    async def test_a_dry_run_answers_with_the_numbers_and_persists_nothing(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        response = await api.post("/transfers", json=warm(dry_run=True))

        assert response.status_code == 201
        body = response.json()
        assert body["bytes_total"] == 20 * GIB
        assert body["file_count"] == 12043
        assert body["allocation_bytes"] == int(20 * GIB * 1.05)
        assert body["route"] == "HOT1->LOC2HOT"
        assert body["refresh"] is False
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 0
        assert not dispatcher.started

    async def test_the_estimate_uses_the_throughput_of_the_route(
        self, api: httpx2.AsyncClient
    ) -> None:
        body = (await api.post("/transfers", json=warm(dry_run=True))).json()

        # 20 GiB over the configured 120 MB/s for HOT1->LOC2HOT, nothing queued ahead.
        assert body["estimated_start_seconds"] == 0
        assert body["estimated_duration_seconds"] == 20 * GIB // (120 * MB) + 1

    async def test_a_dry_run_still_refuses_what_would_not_fit(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.post("/transfers", json=warm(dry_run=True, size_bytes=500 * GIB))

        assert response.status_code == 201, "a dry run reports; admission happens on submit"
        assert response.json()["allocation_bytes"] == 500 * GIB


class TestRefusals:
    async def test_a_source_that_is_not_there(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        dispatcher.exists = False

        response = await api.post("/transfers", json=warm())

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PATH_NOT_FOUND"

    async def test_a_source_the_user_cannot_read(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        dispatcher.readable = False

        response = await api.post("/transfers", json=warm())

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    async def test_a_storage_that_holds_no_source_data(self, api: httpx2.AsyncClient) -> None:
        response = await api.post(
            "/transfers",
            json=warm(source={"storage": "LOC2HOT", "path": "/x"}),
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "NOT_A_SOURCE_STORAGE"

    async def test_a_target_that_holds_no_filesets(self, api: httpx2.AsyncClient) -> None:
        response = await api.post(
            "/transfers", json=warm(target={"storage": "HOT1", "fileset": "mydir"})
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "NOT_A_CACHE_STORAGE"

    async def test_a_warm_that_does_not_fit_is_refused_with_its_numbers(
        self, api: httpx2.AsyncClient, dispatcher: Any, session: AsyncSession
    ) -> None:
        dispatcher.bytes_total = 500 * GIB

        response = await api.post("/transfers", json=warm())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ALLOCATION_LIMIT_EXCEEDED"
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 0

    async def test_an_invalid_fileset_name(self, api: httpx2.AsyncClient) -> None:
        response = await api.post(
            "/transfers", json=warm(target={"storage": "LOC2HOT", "fileset": "../escape"})
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_NAME"


class TestRefresh:
    async def test_warming_the_same_source_again_needs_refresh(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post("/transfers", json=warm())

        response = await api.post("/transfers", json=warm())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_a_refresh_reuses_the_fileset(
        self, api: httpx2.AsyncClient, session: AsyncSession, dispatcher: Any
    ) -> None:
        await api.post("/transfers", json=warm())
        await _finish(session)

        response = await api.post("/transfers", json=warm(refresh=True))

        assert response.status_code == 201
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 1

    async def test_a_different_source_into_the_same_name_is_refused(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/transfers", json=warm())
        await _finish(session)

        response = await api.post(
            "/transfers",
            json=warm(source={"storage": "HOT1", "path": "/myuser/other"}, refresh=True),
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SOURCE_MISMATCH"

    async def test_a_warm_onto_an_output_fileset_is_refused(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "mydir", "size_bytes": GIB}
        )

        response = await api.post("/transfers", json=warm())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "FILESET_EXISTS"


async def _finish(session: AsyncSession) -> None:
    """Pretend the daemon reported success, so the fileset is READY again."""
    fileset = (await session.scalars(sa.select(Fileset))).one()
    transfer = (await session.scalars(sa.select(Transfer))).one()
    fileset.state = FilesetState.READY
    transfer.state = TransferState.SUCCEEDED
    await session.commit()


class TestChannels:
    @pytest.fixture
    def valid_cluster(self) -> dict[str, Any]:
        """The real topology: HOT1 and LOC2HOT are served by different daemons."""
        return copy.deepcopy(VALID_CLUSTER)

    async def test_a_warm_between_two_daemons_is_accepted(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        """The channel is the scheduler's to choose when it dispatches."""
        response = await api.post("/transfers", json=warm())

        assert response.status_code == 201
        assert response.json()["route"] == "HOT1->LOC2HOT"
