"""Every mutating request leaves a record of who did what."""

from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.models import AuditEvent
from stashd.services import audit

pytestmark = pytest.mark.integration

GIB = 1024**3
ADMIN = {"Authorization": "Munge cred-admin"}


async def events(session: AsyncSession) -> list[AuditEvent]:
    return list(await session.scalars(sa.select(AuditEvent).order_by(AuditEvent.id)))


async def a_fileset(api: httpx2.AsyncClient, name: str = "results") -> int:
    response = await api.post(
        "/filesets", json={"storage": "LOC2HOT", "name": name, "size_bytes": 4 * GIB}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


class TestEveryMutationIsRecorded:
    @pytest.mark.parametrize(
        ("action", "subject", "request_"),
        [
            (
                "create",
                "mmustermann",
                lambda api, fileset_id: api.post(
                    "/filesets",
                    json={"storage": "LOC2HOT", "name": "another", "size_bytes": GIB},
                ),
            ),
            (
                "resize",
                "mmustermann",
                lambda api, fileset_id: api.patch(
                    f"/filesets/{fileset_id}", json={"size_bytes": 8 * GIB}
                ),
            ),
            (
                "warm",
                "mmustermann",
                lambda api, fileset_id: api.post(
                    "/transfers",
                    json={
                        "kind": "warm",
                        "source": {"storage": "HOT1", "path": "/mmustermann/mydirectory"},
                        "target": {"storage": "LOC2HOT", "fileset": "warmed"},
                    },
                ),
            ),
            (
                "flush",
                "mmustermann",
                lambda api, fileset_id: api.post(
                    "/transfers",
                    json={
                        "kind": "flush",
                        "source": {"storage": "LOC2HOT", "fileset": "results"},
                        "target": {"storage": "HOT1", "path": "/mmustermann/out"},
                    },
                ),
            ),
            (
                "release",
                "mmustermann",
                lambda api, fileset_id: api.post(
                    "/transfers",
                    json={
                        "kind": "release",
                        "target": {"storage": "LOC2HOT", "fileset": "results"},
                        "discard": True,
                    },
                ),
            ),
            (
                "set_limit",
                "mmustermann",
                lambda api, fileset_id: api.put(
                    "/limits/mmustermann/LOC2HOT",
                    json={"allocation_limit_bytes": GIB},
                    headers=ADMIN,
                ),
            ),
            (
                # A storage belongs to no one: the admin is the subject of their own act.
                "drain",
                "root",
                lambda api, fileset_id: api.post("/storages/LOC2HOT/drain", headers=ADMIN),
            ),
        ],
    )
    async def test_it_says_who_did_what_to_which_object(
        self,
        api: httpx2.AsyncClient,
        session: AsyncSession,
        action: str,
        subject: str,
        request_: Any,
    ) -> None:
        fileset_id = await a_fileset(api)
        before = len(await events(session))

        response = await request_(api, fileset_id)

        assert response.status_code < 400, response.text
        session.expire_all()
        written = (await events(session))[before:]
        assert [event.action for event in written] == [action]
        assert written[0].actor_uid in (0, 1000)
        assert written[0].subject_user == subject
        assert written[0].result == audit.OK

    async def test_a_refusal_is_recorded_with_its_code(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await a_fileset(api)
        before = len(await events(session))

        response = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "results", "size_bytes": GIB}
        )

        assert response.status_code == 409
        session.expire_all()
        written = (await events(session))[before:]
        assert [event.result for event in written] == ["FILESET_EXISTS"]

    async def test_reading_is_not_audited(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await a_fileset(api)
        before = len(await events(session))

        await api.get("/filesets")
        await api.get("/transfers")
        await api.get("/allocations")
        await api.get("/reports/allocation", headers=ADMIN)

        session.expire_all()
        assert len(await events(session)) == before

    async def test_no_credential_ever_reaches_the_record(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await a_fileset(api)

        session.expire_all()
        for event in await events(session):
            assert "cred-" not in str(event.detail)
            assert "Munge" not in str(event.detail)
