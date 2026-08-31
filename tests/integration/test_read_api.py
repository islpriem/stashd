"""The public read API against a real database."""

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.domain.transfers import TransferKind, TransferState
from stashd.models import Fileset, Transfer, UserLimit

pytestmark = pytest.mark.integration

GIB = 1024**3
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def cached(name: str, **overrides: object) -> Fileset:
    values: dict[str, object] = {
        "name": name,
        "owner_user": "mmustermann",
        "owner_uid": 1000,
        "owner_gid": 1000,
        "storage_id": "LOC2HOT",
        "kind": FilesetKind.CACHED,
        "state": FilesetState.READY,
        "path": f"/cache/loc2/mmustermann/{name}",
        "allocated_bytes": 21 * GIB,
        "used_bytes": 20 * GIB,
        "used_bytes_at": T0,
        "file_count": 12043,
        "source_storage_id": "HOT1",
        "source_path": f"/myuser/{name}",
        "created_at": T0,
        "warm_finished_at": T0 + timedelta(hours=2),
    }
    return Fileset(**{**values, **overrides})


async def given(session: AsyncSession, *rows: object) -> None:
    session.add_all(rows)
    await session.commit()


class TestFilesets:
    async def test_everyone_may_read_every_fileset(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mine"), cached("theirs", owner_user="jdoe"))

        response = await api.get("/filesets", headers={"Authorization": "Munge cred-other"})

        assert response.status_code == 200
        assert {entry["name"] for entry in response.json()["filesets"]} == {"mine", "theirs"}

    async def test_a_fileset_carries_its_reference_source_and_history(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))

        entry = (await api.get("/filesets")).json()["filesets"][0]

        assert entry["reference"] == "LOC2HOT:mydir"
        assert entry["source"] == "HOT1:/myuser/mydir"
        assert entry["path"] == "/cache/loc2/mmustermann/mydir"
        assert entry["allocated_bytes"] == 21 * GIB
        assert entry["used_bytes"] == 20 * GIB
        assert entry["file_count"] == 12043
        assert entry["state"] == "READY"
        assert entry["kind"] == "cached"
        assert entry["warm_finished_at"] == "2026-08-31T14:00:00Z"

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ({"storage": "LOC2HOT"}, {"mydir", "results"}),
            ({"storage": "OTHER"}, {"theirs", "broken"}),
            ({"storage": "NOWHERE"}, set()),
            ({"user": "jdoe"}, {"theirs"}),
            ({"kind": "output"}, {"results"}),
            ({"state": "FAILED"}, {"broken"}),
        ],
    )
    async def test_the_list_is_filtered(
        self,
        api: httpx2.AsyncClient,
        session: AsyncSession,
        query: dict[str, str],
        expected: set[str],
    ) -> None:
        await given(
            session,
            cached("mydir"),
            cached(
                "results", kind=FilesetKind.OUTPUT, source_storage_id=None, source_path=None
            ),
            cached("theirs", owner_user="jdoe", storage_id="OTHER"),
            cached("broken", state=FilesetState.FAILED, storage_id="OTHER"),
        )

        response = await api.get("/filesets", params=query)

        assert {entry["name"] for entry in response.json()["filesets"]} == expected

    async def test_an_output_fileset_has_no_source(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(
            session,
            cached(
                "results", kind=FilesetKind.OUTPUT, source_storage_id=None, source_path=None
            ),
        )

        assert (await api.get("/filesets")).json()["filesets"][0]["source"] is None

    async def test_one_fileset_by_id(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))
        listed = (await api.get("/filesets")).json()["filesets"][0]

        response = await api.get(f"/filesets/{listed['id']}")

        assert response.status_code == 200
        assert response.json() == listed

    async def test_an_unknown_fileset_is_not_found(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/filesets/4711")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestEnvelope:
    async def test_a_malformed_query_parameter_is_an_invalid_request(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.get("/filesets", params={"kind": "nonsense"})

        assert response.status_code == 400
        body = response.json()["error"]
        assert body["code"] == "INVALID_REQUEST"
        assert body["details"]["problems"][0]["field"] == "query.kind"

    async def test_a_page_size_beyond_the_maximum_is_refused(
        self, api: httpx2.AsyncClient
    ) -> None:
        assert (await api.get("/transfers", params={"limit": 10_000})).status_code == 400


class TestTransfers:
    async def _fileset_id(self, api: httpx2.AsyncClient) -> int:
        return int((await api.get("/filesets")).json()["filesets"][0]["id"])

    async def test_the_queue_is_visible_to_everyone_newest_first(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))
        fileset_id = await self._fileset_id(api)
        await given(
            session,
            Transfer(
                kind=TransferKind.WARM,
                user="mmustermann",
                fileset_id=fileset_id,
                state=TransferState.SUBMITTED,
                route="HOT1->LOC2HOT",
                bytes_total=21 * GIB,
                submitted_at=T0,
                peer_ref="HOT1:/myuser/mydir",
            ),
            Transfer(
                kind=TransferKind.RELEASE,
                user="jdoe",
                fileset_id=fileset_id,
                state=TransferState.SUCCEEDED,
                route="LOC2HOT->LOC2HOT",
                submitted_at=T0 + timedelta(minutes=1),
            ),
        )

        body = (await api.get("/transfers")).json()

        assert [entry["id"] for entry in body["transfers"]] == [2, 1]
        assert body["transfers"][1]["peer_ref"] == "HOT1:/myuser/mydir"
        assert body["transfers"][1]["bytes_total"] == 21 * GIB
        assert body["next_cursor"] is None

    async def test_the_list_is_filtered_and_paginated(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))
        fileset_id = await self._fileset_id(api)
        await given(
            session,
            *[
                Transfer(
                    kind=TransferKind.WARM,
                    user="mmustermann" if index % 2 else "jdoe",
                    fileset_id=fileset_id,
                    state=TransferState.SUBMITTED,
                    route="HOT1->LOC2HOT",
                    submitted_at=T0 + timedelta(minutes=index),
                )
                for index in range(5)
            ],
        )

        first = (await api.get("/transfers", params={"limit": 2})).json()
        assert [entry["id"] for entry in first["transfers"]] == [5, 4]
        assert first["next_cursor"] == "4"

        second = (await api.get("/transfers", params={"limit": 2, "cursor": "4"})).json()
        assert [entry["id"] for entry in second["transfers"]] == [3, 2]

        mine = (await api.get("/transfers", params={"user": "jdoe"})).json()
        assert [entry["user"] for entry in mine["transfers"]] == ["jdoe"] * 3

    async def test_the_list_is_filtered_by_state_kind_and_storage(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))
        fileset_id = await self._fileset_id(api)
        await given(
            session,
            Transfer(
                kind=TransferKind.WARM,
                user="mmustermann",
                fileset_id=fileset_id,
                state=TransferState.RUNNING,
                route="HOT1->LOC2HOT",
                submitted_at=T0,
            ),
            Transfer(
                kind=TransferKind.RELEASE,
                user="mmustermann",
                fileset_id=fileset_id,
                state=TransferState.SUCCEEDED,
                route="OTHER->OTHER",
                submitted_at=T0,
            ),
        )

        async def ids(**query: str) -> list[int]:
            body = (await api.get("/transfers", params=query)).json()
            return [entry["id"] for entry in body["transfers"]]

        assert await ids(state="RUNNING") == [1]
        assert await ids(kind="release") == [2]
        assert await ids(storage="LOC2HOT") == [1]
        assert await ids(storage="OTHER") == [2]
        assert await ids(storage="HOT1") == [1]

    async def test_one_transfer_by_id(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("mydir"))
        fileset_id = await self._fileset_id(api)
        await given(
            session,
            Transfer(
                kind=TransferKind.WARM,
                user="mmustermann",
                fileset_id=fileset_id,
                state=TransferState.RUNNING,
                route="HOT1->LOC2HOT",
                bytes_total=100,
                bytes_done=40,
                submitted_at=T0,
                started_at=T0,
            ),
        )

        response = await api.get("/transfers/1")

        assert response.status_code == 200
        assert response.json()["bytes_done"] == 40
        assert response.json()["state"] == "RUNNING"

    async def test_an_unknown_transfer_is_not_found(self, api: httpx2.AsyncClient) -> None:
        assert (await api.get("/transfers/4711")).status_code == 404


class TestAllocations:
    async def test_limits_defaults_and_sums_per_storage_and_in_total(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(
            session,
            cached("a", allocated_bytes=10 * GIB, used_bytes=9 * GIB),
            cached("b", allocated_bytes=20 * GIB, used_bytes=1 * GIB),
            cached("gone", state=FilesetState.RELEASED, allocated_bytes=99 * GIB),
        )

        body = (await api.get("/allocations")).json()

        assert body["user"] == "mmustermann"
        storage = next(entry for entry in body["storages"] if entry["storage_id"] == "LOC2HOT")
        assert storage["limit_bytes"] == 100 * GIB
        assert storage["allocated_bytes"] == 30 * GIB
        assert storage["used_bytes"] == 10 * GIB
        assert storage["free_bytes"] == 70 * GIB
        assert body["total"] == {
            "limit_bytes": 250 * GIB,
            "allocated_bytes": 30 * GIB,
            "used_bytes": 10 * GIB,
            "free_bytes": 220 * GIB,
        }

    async def test_a_released_fileset_frees_its_allocation(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("gone", state=FilesetState.RELEASED))

        body = (await api.get("/allocations")).json()

        assert body["total"]["allocated_bytes"] == 0

    async def test_a_user_limit_overrides_the_storage_default(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(
            session,
            UserLimit(
                user="mmustermann", storage_id="LOC2HOT", allocation_limit_bytes=42 * GIB
            ),
            UserLimit(user="mmustermann", storage_id=None, allocation_limit_bytes=64 * GIB),
        )

        body = (await api.get("/allocations")).json()

        storage = next(entry for entry in body["storages"] if entry["storage_id"] == "LOC2HOT")
        assert storage["limit_bytes"] == 42 * GIB
        assert body["total"]["limit_bytes"] == 64 * GIB

    async def test_another_user_may_be_asked_about(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(session, cached("theirs", owner_user="jdoe"))

        body = (await api.get("/allocations", params={"user": "jdoe"})).json()

        assert body["user"] == "jdoe"
        assert body["total"]["allocated_bytes"] == 21 * GIB

    async def test_only_cache_storages_are_listed(self, api: httpx2.AsyncClient) -> None:
        body = (await api.get("/allocations")).json()

        assert [entry["storage_id"] for entry in body["storages"]] == ["LOC2HOT"]


class TestLimits:
    async def test_stored_limits_are_readable_by_everyone(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(
            session,
            UserLimit(user="jdoe", storage_id="LOC2HOT", allocation_limit_bytes=42 * GIB),
            UserLimit(user="mmustermann", storage_id=None, allocation_limit_bytes=64 * GIB),
        )

        body = (await api.get("/limits")).json()

        assert body["limits"] == [
            {"user": "jdoe", "storage_id": "LOC2HOT", "allocation_limit_bytes": 42 * GIB},
            {"user": "mmustermann", "storage_id": None, "allocation_limit_bytes": 64 * GIB},
        ]

    async def test_the_list_can_be_narrowed_to_one_user(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await given(
            session,
            UserLimit(user="jdoe", storage_id="LOC2HOT", allocation_limit_bytes=42 * GIB),
            UserLimit(user="mmustermann", storage_id=None, allocation_limit_bytes=64 * GIB),
        )

        body = (await api.get("/limits", params={"user": "jdoe"})).json()

        assert [entry["user"] for entry in body["limits"]] == ["jdoe"]
