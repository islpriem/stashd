"""cool: what a release refuses, and what a flush does."""

from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.domain.filesets import FilesetKind, FilesetState
from stashd.models import Fileset

pytestmark = pytest.mark.asyncio

GIB = 1024**3
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def output_fileset(api: httpx2.AsyncClient, name: str = "results") -> dict[str, object]:
    response = await api.post(
        "/filesets", json={"storage": "LOC2HOT", "name": name, "size_bytes": 4 * GIB}
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def cached_fileset(session: AsyncSession, name: str = "mydir") -> Fileset:
    """A warmed fileset, as it stands once its warm has finished."""
    fileset = Fileset(
        name=name,
        owner_user="mmustermann",
        owner_uid=1000,
        owner_gid=1000,
        storage_id="LOC2HOT",
        kind=FilesetKind.CACHED,
        state=FilesetState.READY,
        path=f"/fake/cache/mmustermann/{name}",
        allocated_bytes=4 * GIB,
        used_bytes=3 * GIB,
        source_storage_id="HOT1",
        source_path="/mmustermann/mydirectory",
        created_at=T0,
        warm_finished_at=T0,
    )
    session.add(fileset)
    await session.commit()
    return fileset


async def release(api: httpx2.AsyncClient, name: str, **extra: object) -> httpx2.Response:
    return await api.post(
        "/transfers",
        json={
            "kind": "release",
            "target": {"storage": "LOC2HOT", "fileset": name},
            **extra,
        },
    )


class TestReleasingAnOutputFileset:
    async def test_it_is_refused_without_somewhere_for_the_data_to_go(
        self, api: httpx2.AsyncClient
    ) -> None:
        await output_fileset(api)

        response = await release(api, "results")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FLUSH_TARGET_REQUIRED"
        listed = (await api.get("/filesets", params={"name": "results"})).json()["filesets"]
        assert listed[0]["state"] == "READY", "nothing was deleted"

    async def test_discard_says_the_data_is_not_wanted(self, api: httpx2.AsyncClient) -> None:
        await output_fileset(api)

        response = await release(api, "results", discard=True)

        assert response.status_code == 201
        assert response.json()["state"] == "SUCCEEDED"
        listed = (await api.get("/filesets", params={"name": "results"})).json()["filesets"]
        assert listed[0]["state"] == "RELEASED"

    async def test_the_rule_is_the_servers_no_matter_who_asks(
        self, api: httpx2.AsyncClient
    ) -> None:
        """An admin is not a way around it: the data is still unreplicated."""
        await output_fileset(api)

        response = await api.post(
            "/transfers",
            json={
                "kind": "release",
                "target": {"storage": "LOC2HOT", "fileset": "results"},
                "user": "mmustermann",
            },
            headers={"Authorization": "Munge cred-admin"},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FLUSH_TARGET_REQUIRED"


class TestReleasingACachedFileset:
    async def test_a_cached_fileset_needs_no_discard(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        """It is reconstructible from its source, so losing it costs nothing."""
        await cached_fileset(session)

        response = await release(api, "mydir")

        assert response.status_code == 201
        assert response.json()["state"] == "SUCCEEDED"

    async def test_discard_on_a_cached_fileset_changes_nothing(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await cached_fileset(session)

        response = await release(api, "mydir", discard=True)

        assert response.status_code == 201


async def flush(
    api: httpx2.AsyncClient,
    name: str = "results",
    path: str = "/mmustermann/out",
    **extra: object,
) -> httpx2.Response:
    return await api.post(
        "/transfers",
        json={
            "kind": "flush",
            "source": {"storage": "LOC2HOT", "fileset": name},
            "target": {"storage": "HOT1", "path": path},
            **extra,
        },
    )


class TestFlushing:
    async def test_it_is_queued_like_any_other_transfer(self, api: httpx2.AsyncClient) -> None:
        await output_fileset(api)

        response = await flush(api)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "flush"
        assert body["state"] == "SUBMITTED"
        assert body["route"] == "LOC2HOT->HOT1"
        assert body["peer_ref"] == "HOT1:/mmustermann/out"

    async def test_the_fileset_is_flushing_while_it_runs(self, api: httpx2.AsyncClient) -> None:
        await output_fileset(api)

        await flush(api)

        listed = (await api.get("/filesets", params={"name": "results"})).json()["filesets"]
        assert listed[0]["state"] == "FLUSHING"

    async def test_the_target_must_be_a_source_storage(self, api: httpx2.AsyncClient) -> None:
        await output_fileset(api)

        response = await api.post(
            "/transfers",
            json={
                "kind": "flush",
                "source": {"storage": "LOC2HOT", "fileset": "results"},
                "target": {"storage": "LOC2HOT", "path": "/mmustermann/out"},
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "NOT_A_SOURCE_STORAGE"

    async def test_a_target_the_user_cannot_write_is_refused(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        await output_fileset(api)
        dispatcher.writable = False

        response = await flush(api)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    async def test_a_target_that_is_a_file_is_refused(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        """Merging into a directory is the interim decision; a file is not one."""
        await output_fileset(api)
        dispatcher.is_dir = False

        response = await flush(api)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PATH"

    async def test_a_target_that_is_not_there_is_refused(
        self, api: httpx2.AsyncClient, dispatcher: Any
    ) -> None:
        await output_fileset(api)
        dispatcher.exists = False

        response = await flush(api)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PATH_NOT_FOUND"

    async def test_flushing_someone_elses_fileset_is_refused(
        self, api: httpx2.AsyncClient
    ) -> None:
        await output_fileset(api)

        response = await api.post(
            "/transfers",
            json={
                "kind": "flush",
                "source": {"storage": "LOC2HOT", "fileset": "results"},
                "target": {"storage": "HOT1", "path": "/jdoe/out"},
            },
            headers={"Authorization": "Munge cred-other"},
        )

        assert response.status_code == 404, "jdoe has no fileset of that name"

    async def test_a_dry_run_persists_nothing(self, api: httpx2.AsyncClient) -> None:
        await output_fileset(api)

        response = await flush(api, dry_run=True)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "flush"
        assert body["target"] == "HOT1:/mmustermann/out"
        assert (await api.get("/transfers")).json()["transfers"] == []
        listed = (await api.get("/filesets", params={"name": "results"})).json()["filesets"]
        assert listed[0]["state"] == "READY", "still untouched"
