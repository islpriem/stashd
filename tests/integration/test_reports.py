"""Usage and allocation reports, for admins."""

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer

pytestmark = pytest.mark.integration

GIB = 1024**3
ADMIN = {"Authorization": "Munge cred-admin"}
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def moved(
    session: AsyncSession,
    *,
    user: str = "mmustermann",
    route: str = "HOT1->LOC2HOT",
    storage: str = "LOC2HOT",
    kind: TransferKind = TransferKind.WARM,
    state: TransferState = TransferState.SUCCEEDED,
    bytes_done: int = 10 * GIB,
    waited: timedelta = timedelta(minutes=1),
    ran: timedelta = timedelta(minutes=10),
    submitted: datetime = T0,
) -> None:
    fileset = Fileset(
        name=f"fs{int(submitted.timestamp())}{user}{state}",
        owner_user=user,
        owner_uid=1000,
        owner_gid=1000,
        storage_id=storage,
        kind=FilesetKind.CACHED,
        state=FilesetState.READY,
        path="/fake/cache/x",
        allocated_bytes=20 * GIB,
        created_at=submitted,
    )
    session.add(fileset)
    await session.flush()
    session.add(
        Transfer(
            kind=kind,
            user=user,
            fileset_id=fileset.id,
            state=state,
            route=route,
            bytes_total=bytes_done,
            bytes_done=bytes_done,
            submitted_at=submitted,
            started_at=submitted + waited,
            finished_at=submitted + waited + ran,
        )
    )
    await session.commit()


class TestUsageReport:
    async def test_it_counts_bytes_transfers_and_the_success_rate(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await moved(session)
        await moved(session, state=TransferState.FAILED, bytes_done=0)

        body = (await api.get("/reports/usage", headers=ADMIN)).json()

        assert body["group_by"] == "user"
        row = body["groups"][0]
        assert row["key"] == "mmustermann"
        assert row["bytes_transferred"] == 10 * GIB
        assert row["transfers"] == 2
        assert row["succeeded"] == 1
        assert row["success_rate"] == 0.5

    async def test_it_reports_how_long_things_waited_and_how_fast_they_ran(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await moved(session, waited=timedelta(minutes=2), ran=timedelta(minutes=10))

        row = (await api.get("/reports/usage", headers=ADMIN)).json()["groups"][0]

        assert row["mean_queue_wait_seconds"] == 120
        assert row["p95_queue_wait_seconds"] == 120
        assert row["mean_throughput_bytes_per_s"] == pytest.approx(10 * GIB / 600, rel=0.01)

    @pytest.mark.parametrize(
        ("group_by", "key"),
        [
            ("user", "mmustermann"),
            ("storage", "LOC2HOT"),
            ("route", "HOT1->LOC2HOT"),
            ("location", "LOC2"),
        ],
    )
    async def test_it_groups_the_way_it_was_asked(
        self, api: httpx2.AsyncClient, session: AsyncSession, group_by: str, key: str
    ) -> None:
        await moved(session)

        body = (
            await api.get("/reports/usage", params={"group_by": group_by}, headers=ADMIN)
        ).json()

        assert body["group_by"] == group_by
        assert [row["key"] for row in body["groups"]] == [key]

    async def test_the_window_leaves_out_what_is_outside_it(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await moved(session, submitted=T0 - timedelta(days=30))
        await moved(session, submitted=T0)

        body = (
            await api.get(
                "/reports/usage",
                params={"since": (T0 - timedelta(days=1)).isoformat()},
                headers=ADMIN,
            )
        ).json()

        assert body["groups"][0]["transfers"] == 1

    async def test_a_window_with_nothing_in_it_is_empty_not_an_error(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.get("/reports/usage", headers=ADMIN)

        assert response.status_code == 200
        assert response.json()["groups"] == []

    async def test_a_user_may_not_read_it(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/reports/usage")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_an_unknown_grouping_is_a_usage_error(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/reports/usage", params={"group_by": "colour"}, headers=ADMIN)

        assert response.status_code == 400


class TestAllocationReport:
    async def test_it_shows_allocation_against_usage_per_user_and_storage(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = Fileset(
            name="results",
            owner_user="mmustermann",
            owner_uid=1000,
            owner_gid=1000,
            storage_id="LOC2HOT",
            kind=FilesetKind.OUTPUT,
            state=FilesetState.READY,
            path="/fake/cache/mmustermann/results",
            allocated_bytes=4 * GIB,
            used_bytes=3 * GIB,
            created_at=T0,
        )
        session.add(fileset)
        await session.commit()

        body = (await api.get("/reports/allocation", headers=ADMIN)).json()

        assert body["rows"] == [
            {
                "user": "mmustermann",
                "storage_id": "LOC2HOT",
                "allocated_bytes": 4 * GIB,
                "used_bytes": 3 * GIB,
                "filesets": 1,
                "limit_bytes": 100 * GIB,
            }
        ]
        assert body["offenders"] == []

    async def test_it_names_the_filesets_past_their_allocation(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = Fileset(
            name="toobig",
            owner_user="jdoe",
            owner_uid=1001,
            owner_gid=1001,
            storage_id="LOC2HOT",
            kind=FilesetKind.OUTPUT,
            state=FilesetState.READY,
            path="/fake/cache/jdoe/toobig",
            allocated_bytes=GIB,
            used_bytes=3 * GIB,
            over_allocation=True,
            created_at=T0,
        )
        session.add(fileset)
        await session.commit()

        body = (await api.get("/reports/allocation", headers=ADMIN)).json()

        assert body["offenders"] == [
            {
                "user": "jdoe",
                "storage_id": "LOC2HOT",
                "fileset": "toobig",
                "allocated_bytes": GIB,
                "used_bytes": 3 * GIB,
            }
        ]

    async def test_released_filesets_are_not_counted(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        session.add(
            Fileset(
                name="gone",
                owner_user="mmustermann",
                owner_uid=1000,
                owner_gid=1000,
                storage_id="LOC2HOT",
                kind=FilesetKind.OUTPUT,
                state=FilesetState.RELEASED,
                path="/fake/cache/mmustermann/gone",
                allocated_bytes=4 * GIB,
                created_at=T0,
            )
        )
        await session.commit()

        assert (await api.get("/reports/allocation", headers=ADMIN)).json()["rows"] == []

    async def test_a_user_may_not_read_it(self, api: httpx2.AsyncClient) -> None:
        assert (await api.get("/reports/allocation")).status_code == 403
