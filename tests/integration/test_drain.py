"""Draining a storage: no new filesets, no new dispatches, running work continues."""

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.models import StorageState

pytestmark = pytest.mark.asyncio


async def drained_ids(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        sa.select(StorageState.storage_id).where(StorageState.drained.is_(True))
    )
    return set(rows.scalars())


class TestDraining:
    async def test_an_admin_drains_a_storage(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        response = await api.post(
            "/storages/LOC2HOT/drain", headers={"Authorization": "Munge cred-admin"}
        )

        assert response.status_code == 200
        assert response.json()["drained"] is True
        assert await drained_ids(session) == {"LOC2HOT"}

    async def test_a_user_may_not(self, api: httpx2.AsyncClient) -> None:
        response = await api.post("/storages/LOC2HOT/drain")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_draining_an_unknown_storage_is_a_404(self, api: httpx2.AsyncClient) -> None:
        response = await api.post(
            "/storages/NOPE/drain", headers={"Authorization": "Munge cred-admin"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_draining_twice_is_the_same_answer(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        admin = {"Authorization": "Munge cred-admin"}
        await api.post("/storages/LOC2HOT/drain", headers=admin)

        response = await api.post("/storages/LOC2HOT/drain", headers=admin)

        assert response.status_code == 200
        assert await drained_ids(session) == {"LOC2HOT"}

    async def test_undraining_lets_it_take_work_again(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        admin = {"Authorization": "Munge cred-admin"}
        await api.post("/storages/LOC2HOT/drain", headers=admin)

        response = await api.post("/storages/LOC2HOT/undrain", headers=admin)

        assert response.status_code == 200
        assert response.json()["drained"] is False
        assert await drained_ids(session) == set()


class TestWhatDrainingRefuses:
    async def test_no_new_fileset_on_a_drained_storage(self, api: httpx2.AsyncClient) -> None:
        await api.post("/storages/LOC2HOT/drain", headers={"Authorization": "Munge cred-admin"})

        response = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "results", "size_bytes": 1024}
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "STORAGE_DRAINED"

    async def test_the_storage_list_says_which_are_drained(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post("/storages/LOC2HOT/drain", headers={"Authorization": "Munge cred-admin"})

        storages = (await api.get("/storages")).json()["storages"]

        assert {storage["id"]: storage["drained"] for storage in storages}["LOC2HOT"] is True

    async def test_draining_one_storage_leaves_the_others_alone(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post("/storages/HOT1/drain", headers={"Authorization": "Munge cred-admin"})

        response = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "results", "size_bytes": 1024}
        )

        assert response.status_code == 201
