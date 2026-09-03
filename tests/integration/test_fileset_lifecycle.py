"""Creating an output fileset and releasing it."""

import asyncio
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.clients.filesets import DaemonUnavailable
from stashd.domain.filesets import FilesetState
from stashd.domain.transfers import TransferState
from stashd.models import AuditEvent, Fileset, Transfer, UserLimit

pytestmark = pytest.mark.integration

GIB = 1024**3


def creation(name: str = "results", size: int = 10 * GIB, **extra: Any) -> dict[str, Any]:
    return {"storage": "LOC2HOT", "name": name, "size_bytes": size, **extra}


class TestCreate:
    async def test_a_fileset_is_created_ready_with_its_path(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.post("/filesets", json=creation())

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "results"
        assert body["state"] == "READY"
        assert body["kind"] == "output"
        assert body["owner_user"] == "mmustermann"
        assert body["allocated_bytes"] == 10 * GIB
        assert body["path"] == "/fake/cache/mmustermann/results"
        assert body["reference"] == "LOC2HOT:results"

    async def test_the_directory_is_created_on_the_daemon(
        self, api: httpx2.AsyncClient, fake_driver: Any
    ) -> None:
        await api.post("/filesets", json=creation())

        assert "/fake/cache/mmustermann/results" in fake_driver.filesets

    async def test_the_quota_is_applied_where_the_driver_can(
        self, api: httpx2.AsyncClient, fake_driver: Any
    ) -> None:
        await api.post("/filesets", json=creation())

        assert fake_driver.filesets["/fake/cache/mmustermann/results"].quota_bytes == 10 * GIB

    async def test_the_same_name_twice_is_a_conflict(self, api: httpx2.AsyncClient) -> None:
        await api.post("/filesets", json=creation())

        response = await api.post("/filesets", json=creation())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "FILESET_EXISTS"

    async def test_a_released_name_can_be_used_again(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        first = (await api.post("/filesets", json=creation())).json()
        await api.post(
            "/transfers",
            json={"kind": "release", "target": {"storage": "LOC2HOT", "fileset": "results"}},
        )

        assert (await api.post("/filesets", json=creation())).status_code == 201
        assert (
            first["id"]
            != (await api.get("/filesets", params={"state": "READY"})).json()["filesets"][0][
                "id"
            ]
        )

    async def test_an_oversized_fileset_is_refused_with_its_numbers(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.post("/filesets", json=creation(size=200 * GIB))

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "ALLOCATION_LIMIT_EXCEEDED"
        assert error["details"] == {
            "required_bytes": 200 * GIB,
            "free_bytes": 100 * GIB,
            "limit_bytes": 100 * GIB,
            "scope": "storage",
            "storage": "LOC2HOT",
        }

    async def test_the_reservation_counts_even_though_nothing_is_written(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post("/filesets", json=creation(name="first", size=60 * GIB))

        response = await api.post("/filesets", json=creation(name="second", size=60 * GIB))

        assert response.status_code == 409
        assert response.json()["error"]["details"]["free_bytes"] == 40 * GIB

    async def test_a_daemon_failure_leaves_no_reservation(
        self, api: httpx2.AsyncClient, session: AsyncSession, fake_driver: Any
    ) -> None:
        fake_driver.fail_on("create_fileset", DaemonUnavailable("loc2hot is down"))

        response = await api.post("/filesets", json=creation())

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DAEMON_UNAVAILABLE"
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 0

    async def test_a_storage_that_is_not_a_cache_is_refused(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.post("/filesets", json={**creation(), "storage": "HOT1"})

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "NOT_A_CACHE_STORAGE"

    async def test_an_unknown_storage_is_not_found(self, api: httpx2.AsyncClient) -> None:
        response = await api.post("/filesets", json={**creation(), "storage": "NOWHERE"})

        assert response.status_code == 404

    async def test_an_invalid_name_is_refused_before_anything_is_persisted(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        response = await api.post("/filesets", json=creation(name="../escape"))

        assert response.status_code == 400
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 0

    async def test_a_user_limit_overrides_the_storage_default(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        session.add(
            UserLimit(user="mmustermann", storage_id="LOC2HOT", allocation_limit_bytes=1 * GIB)
        )
        await session.commit()

        response = await api.post("/filesets", json=creation(size=2 * GIB))

        assert response.status_code == 409
        assert response.json()["error"]["details"]["limit_bytes"] == 1 * GIB

    async def test_only_an_admin_may_create_for_someone_else(
        self, api: httpx2.AsyncClient
    ) -> None:
        refused = await api.post("/filesets", json=creation(user="jdoe"))
        assert refused.status_code == 403

        allowed = await api.post(
            "/filesets",
            json=creation(user="jdoe"),
            headers={"Authorization": "Munge cred-admin"},
        )
        assert allowed.status_code == 201
        assert allowed.json()["owner_user"] == "jdoe"

    async def test_creating_is_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/filesets", json=creation())

        event = (await session.scalars(sa.select(AuditEvent))).one()
        assert (event.actor_uid, event.subject_user) == (1000, "mmustermann")
        assert (event.object_type, event.action, event.result) == ("fileset", "create", "ok")

    async def test_a_refusal_is_audited_too(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/filesets", json=creation(size=200 * GIB))

        event = (await session.scalars(sa.select(AuditEvent))).one()
        assert event.result == "ALLOCATION_LIMIT_EXCEEDED"


class TestRelease:
    async def released(self, api: httpx2.AsyncClient, name: str = "results") -> httpx2.Response:
        return await api.post(
            "/transfers",
            json={"kind": "release", "target": {"storage": "LOC2HOT", "fileset": name}},
        )

    async def test_the_fileset_is_gone_and_the_allocation_is_free(
        self, api: httpx2.AsyncClient, fake_driver: Any
    ) -> None:
        await api.post("/filesets", json=creation())

        response = await self.released(api)

        assert response.status_code == 201
        assert response.json()["kind"] == "release"
        assert response.json()["state"] == "SUCCEEDED"
        assert fake_driver.filesets == {}
        assert (await api.get("/allocations")).json()["total"]["allocated_bytes"] == 0

    async def test_the_fileset_ends_up_released(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/filesets", json=creation())

        await self.released(api)

        fileset = (await session.scalars(sa.select(Fileset))).one()
        assert fileset.state is FilesetState.RELEASED
        assert fileset.released_at is not None

    async def test_a_failed_delete_keeps_the_reservation(
        self, api: httpx2.AsyncClient, session: AsyncSession, fake_driver: Any
    ) -> None:
        await api.post("/filesets", json=creation())
        fake_driver.fail_on("delete_fileset", DaemonUnavailable("loc2hot is down"))

        response = await self.released(api)

        assert response.status_code == 503
        session.expire_all()
        fileset = (await session.scalars(sa.select(Fileset))).one()
        assert fileset.state is FilesetState.FAILED
        transfer = (await session.scalars(sa.select(Transfer))).one()
        assert transfer.state is TransferState.FAILED
        assert (await api.get("/allocations")).json()["total"]["allocated_bytes"] == 10 * GIB

    async def test_naming_another_user_is_admin_only(self, api: httpx2.AsyncClient) -> None:
        await api.post("/filesets", json=creation())

        response = await api.post(
            "/transfers",
            json={
                "kind": "release",
                "target": {"storage": "LOC2HOT", "fileset": "results"},
                "user": "mmustermann",
            },
            headers={"Authorization": "Munge cred-other"},
        )

        assert response.status_code == 403

    async def test_another_users_reference_names_nothing_of_ones_own(
        self, api: httpx2.AsyncClient
    ) -> None:
        """LOC2HOT:results means the caller's own fileset, so jdoe has no such thing."""
        await api.post("/filesets", json=creation())

        response = await api.post(
            "/transfers",
            json={"kind": "release", "target": {"storage": "LOC2HOT", "fileset": "results"}},
            headers={"Authorization": "Munge cred-other"},
        )

        assert response.status_code == 404

    async def test_an_admin_may_release_for_another_user(self, api: httpx2.AsyncClient) -> None:
        await api.post("/filesets", json=creation())

        response = await api.post(
            "/transfers",
            json={
                "kind": "release",
                "target": {"storage": "LOC2HOT", "fileset": "results"},
                "user": "mmustermann",
            },
            headers={"Authorization": "Munge cred-admin"},
        )

        assert response.status_code == 201

    async def test_an_unknown_fileset_is_not_found(self, api: httpx2.AsyncClient) -> None:
        assert (await self.released(api, "absent")).status_code == 404

    async def test_releasing_twice_is_a_conflict(self, api: httpx2.AsyncClient) -> None:
        await api.post("/filesets", json=creation())
        await self.released(api)

        assert (await self.released(api)).status_code == 404

    async def test_a_kind_the_server_does_not_know_is_refused(
        self, api: httpx2.AsyncClient
    ) -> None:
        response = await api.post(
            "/transfers",
            json={"kind": "teleport", "target": {"storage": "LOC2HOT", "fileset": "mydir"}},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    async def test_releasing_is_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await api.post("/filesets", json=creation())

        await self.released(api)

        actions = list(
            await session.scalars(sa.select(AuditEvent.action).order_by(AuditEvent.id))
        )
        assert actions == ["create", "release"]


class TestAdmissionRaces:
    async def test_concurrent_creates_that_jointly_exceed_a_limit_admit_one(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        """Two requests that each fit but together do not: exactly one may win."""
        both = await asyncio.gather(
            api.post("/filesets", json=creation(name="first", size=60 * GIB)),
            api.post("/filesets", json=creation(name="second", size=60 * GIB)),
        )

        codes = sorted(response.status_code for response in both)
        assert codes == [201, 409]
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 1

    async def test_concurrent_creates_of_the_same_name_admit_one(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        both = await asyncio.gather(
            api.post("/filesets", json=creation(size=1 * GIB)),
            api.post("/filesets", json=creation(size=1 * GIB)),
        )

        assert sorted(response.status_code for response in both) == [201, 409]
        assert (await session.scalar(sa.select(sa.func.count()).select_from(Fileset))) == 1
