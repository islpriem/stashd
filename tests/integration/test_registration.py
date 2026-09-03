"""Daemons announcing themselves, and what that makes visible."""

from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.models import Daemon

pytestmark = pytest.mark.integration


def registration(**extra: Any) -> dict[str, Any]:
    return {
        "daemon_id": "hot1",
        "storages": ["HOT1"],
        "config_revision": 42,
        "version": "0.1.0",
        **extra,
    }


class TestRegister:
    async def test_a_daemon_announces_itself(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        response = await peer.post("/register", json=registration())

        assert response.status_code == 200
        assert response.json()["config_revision"] == 42
        daemon = (await session.scalars(sa.select(Daemon))).one()
        assert daemon.id == "hot1"
        assert daemon.storages == ["HOT1"]
        assert daemon.last_seen_at is not None

    async def test_registering_again_updates_what_is_known(
        self, peer: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        await peer.post("/register", json=registration(config_revision=41))

        await peer.post("/register", json=registration(config_revision=42))

        session.expire_all()
        daemon = (await session.scalars(sa.select(Daemon))).one()
        assert daemon.config_revision == 42

    async def test_a_daemon_on_an_older_revision_is_told_to_refresh(
        self, peer: httpx2.AsyncClient
    ) -> None:
        response = await peer.post("/register", json=registration(config_revision=41))

        assert response.status_code == 200
        body = response.json()
        assert body["config_revision"] == 42, "the revision the controller is on"
        assert body["refresh_needed"] is True

    async def test_a_daemon_claiming_a_storage_it_does_not_serve_is_refused(
        self, peer: httpx2.AsyncClient
    ) -> None:
        response = await peer.post("/register", json=registration(storages=["LOC2HOT"]))

        assert response.status_code == 409
        assert "loc2hot" in response.json()["error"]["message"]

    async def test_an_unknown_daemon_is_not_found(self, peer: httpx2.AsyncClient) -> None:
        response = await peer.post("/register", json=registration(daemon_id="stranger"))

        assert response.status_code == 404

    async def test_registration_needs_the_peer_token(self, api: httpx2.AsyncClient) -> None:
        response = await api.post("http://controller/internal/v1/register", json=registration())

        assert response.status_code == 401


class TestVisibility:
    async def test_storages_report_the_daemon_that_serves_them(
        self, api: httpx2.AsyncClient, peer: httpx2.AsyncClient
    ) -> None:
        await peer.post("/register", json=registration())

        storages = {row["id"]: row for row in (await api.get("/storages")).json()["storages"]}

        assert storages["HOT1"]["daemon"] == "hot1"
        assert storages["HOT1"]["daemon_seen_at"] is not None
        assert storages["HOT1"]["daemon_config_revision"] == 42
        assert storages["LOC2HOT"]["daemon_seen_at"] is None, "loc2hot has not registered"
