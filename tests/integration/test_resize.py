"""Resizing a fileset's allocation."""

from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.models import AuditEvent, Fileset

pytestmark = pytest.mark.integration

GIB = 1024**3


async def created(api: httpx2.AsyncClient, size: int = 10 * GIB) -> dict[str, Any]:
    response = await api.post(
        "/filesets", json={"storage": "LOC2HOT", "name": "results", "size_bytes": size}
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def used(session: AsyncSession, fileset_id: int, bytes_used: int) -> None:
    fileset = await session.get(Fileset, fileset_id)
    assert fileset is not None
    fileset.used_bytes = bytes_used
    await session.commit()


class TestGrowing:
    async def test_growing_changes_the_allocation(self, api: httpx2.AsyncClient) -> None:
        fileset = await created(api)

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 20 * GIB})

        assert response.status_code == 200
        assert response.json()["allocated_bytes"] == 20 * GIB

    async def test_growing_beyond_the_limit_is_refused_with_its_numbers(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = await created(api)

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 200 * GIB})

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "ALLOCATION_LIMIT_EXCEEDED"
        assert error["details"]["required_bytes"] == 190 * GIB, "only the growth is asked for"
        assert error["details"]["free_bytes"] == 90 * GIB
        session.expire_all()
        stored = await session.get(Fileset, fileset["id"])
        assert stored is not None and stored.allocated_bytes == 10 * GIB

    async def test_the_reservation_of_other_filesets_counts(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "other", "size_bytes": 60 * GIB}
        )
        fileset = await created(api)

        # 70 GiB is reserved of a 100 GiB limit, so 30 GiB of growth is all that fits.
        assert (
            await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 40 * GIB})
        ).status_code == 200

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 45 * GIB})

        assert response.status_code == 409


class TestShrinking:
    async def test_shrinking_above_what_is_used_is_allowed(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = await created(api)
        await used(session, fileset["id"], 2 * GIB)

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 5 * GIB})

        assert response.status_code == 200
        assert response.json()["allocated_bytes"] == 5 * GIB

    async def test_shrinking_below_what_is_used_is_refused(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = await created(api)
        await used(session, fileset["id"], 8 * GIB)

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 4 * GIB})

        assert response.status_code == 409
        assert response.json()["error"]["details"]["used_bytes"] == 8 * GIB

    async def test_an_admin_may_force_it(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = await created(api)
        await used(session, fileset["id"], 8 * GIB)

        refused = await api.patch(
            f"/filesets/{fileset['id']}", json={"size_bytes": 4 * GIB, "force": True}
        )
        assert refused.status_code == 403, "force is not the caller's to use"

        allowed = await api.patch(
            f"/filesets/{fileset['id']}",
            json={"size_bytes": 4 * GIB, "force": True},
            headers={"Authorization": "Munge cred-admin"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["allocated_bytes"] == 4 * GIB


class TestPermissionsAndState:
    async def test_only_the_owner_or_an_admin_may_resize(self, api: httpx2.AsyncClient) -> None:
        fileset = await created(api)

        response = await api.patch(
            f"/filesets/{fileset['id']}",
            json={"size_bytes": 5 * GIB},
            headers={"Authorization": "Munge cred-other"},
        )

        assert response.status_code == 403

    async def test_an_admin_may_resize_anyones_fileset(self, api: httpx2.AsyncClient) -> None:
        fileset = await created(api)

        response = await api.patch(
            f"/filesets/{fileset['id']}",
            json={"size_bytes": 5 * GIB},
            headers={"Authorization": "Munge cred-admin"},
        )

        assert response.status_code == 200

    async def test_a_released_fileset_cannot_be_resized(self, api: httpx2.AsyncClient) -> None:
        fileset = await created(api)
        await api.post(
            "/transfers",
            json={
                "kind": "release",
                "target": {"storage": "LOC2HOT", "fileset": "results"},
                "discard": True,
            },
        )

        response = await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 5 * GIB})

        assert response.status_code == 409

    async def test_an_unknown_fileset_is_not_found(self, api: httpx2.AsyncClient) -> None:
        assert (await api.patch("/filesets/4711", json={"size_bytes": GIB})).status_code == 404

    async def test_a_size_of_zero_is_refused(self, api: httpx2.AsyncClient) -> None:
        fileset = await created(api)

        assert (
            await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 0})
        ).status_code == 400


class TestQuotaAndAudit:
    async def test_the_storage_is_told_about_the_new_allocation(
        self, api: httpx2.AsyncClient, fake_driver: Any
    ) -> None:
        fileset = await created(api)

        await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 20 * GIB})

        assert fake_driver.filesets[fileset["path"]].quota_bytes == 20 * GIB

    async def test_a_resize_is_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        fileset = await created(api)

        await api.patch(f"/filesets/{fileset['id']}", json={"size_bytes": 20 * GIB})

        actions = list(
            await session.scalars(sa.select(AuditEvent.action).order_by(AuditEvent.id))
        )
        assert actions == ["create", "resize"]
