"""Admins set, raise, lower and clear allocation limits."""

import httpx2
import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"Authorization": "Munge cred-admin"}
GIB = 1024**3


async def limits_of(api: httpx2.AsyncClient, user: str) -> dict[str | None, int]:
    body = (await api.get("/limits", params={"user": user})).json()
    return {row["storage_id"]: row["allocation_limit_bytes"] for row in body["limits"]}


class TestSettingALimit:
    async def test_an_admin_sets_a_per_storage_limit(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/mmustermann/LOC2HOT",
            json={"allocation_limit_bytes": 5 * GIB},
            headers=ADMIN,
        )

        assert response.status_code == 200
        assert response.json() == {
            "user": "mmustermann",
            "storage_id": "LOC2HOT",
            "allocation_limit_bytes": 5 * GIB,
        }
        assert await limits_of(api, "mmustermann") == {"LOC2HOT": 5 * GIB}

    async def test_an_admin_sets_the_cluster_wide_limit(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/mmustermann", json={"allocation_limit_bytes": 9 * GIB}, headers=ADMIN
        )

        assert response.status_code == 200
        assert await limits_of(api, "mmustermann") == {None: 9 * GIB}

    async def test_setting_it_again_replaces_it(self, api: httpx2.AsyncClient) -> None:
        await api.put(
            "/limits/mmustermann/LOC2HOT",
            json={"allocation_limit_bytes": 5 * GIB},
            headers=ADMIN,
        )

        await api.put(
            "/limits/mmustermann/LOC2HOT",
            json={"allocation_limit_bytes": 7 * GIB},
            headers=ADMIN,
        )

        assert await limits_of(api, "mmustermann") == {"LOC2HOT": 7 * GIB}

    async def test_a_user_may_not_set_their_own(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/mmustermann/LOC2HOT", json={"allocation_limit_bytes": 500 * GIB}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert await limits_of(api, "mmustermann") == {}

    async def test_an_unknown_storage_is_refused(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/mmustermann/NOPE", json={"allocation_limit_bytes": GIB}, headers=ADMIN
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_an_unknown_user_is_refused(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/nobody/LOC2HOT", json={"allocation_limit_bytes": GIB}, headers=ADMIN
        )

        assert response.status_code == 404


class TestClearingALimit:
    async def test_clearing_returns_to_the_configured_default(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.put(
            "/limits/mmustermann/LOC2HOT",
            json={"allocation_limit_bytes": 5 * GIB},
            headers=ADMIN,
        )

        response = await api.delete("/limits/mmustermann/LOC2HOT", headers=ADMIN)

        assert response.status_code == 204
        assert await limits_of(api, "mmustermann") == {}
        allocations = (await api.get("/allocations")).json()
        assert allocations["storages"][0]["limit_bytes"] == 100 * GIB

    async def test_clearing_what_was_never_set_is_a_404(self, api: httpx2.AsyncClient) -> None:
        response = await api.delete("/limits/mmustermann/LOC2HOT", headers=ADMIN)

        assert response.status_code == 404

    async def test_a_user_may_not_clear_a_limit(self, api: httpx2.AsyncClient) -> None:
        await api.put(
            "/limits/mmustermann/LOC2HOT", json={"allocation_limit_bytes": GIB}, headers=ADMIN
        )

        response = await api.delete("/limits/mmustermann/LOC2HOT")

        assert response.status_code == 403


class TestLoweringBelowWhatIsHeld:
    async def test_lowering_blocks_new_allocations_but_keeps_the_data(
        self, api: httpx2.AsyncClient
    ) -> None:
        await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "kept", "size_bytes": 4 * GIB}
        )

        lowered = await api.put(
            "/limits/mmustermann/LOC2HOT", json={"allocation_limit_bytes": GIB}, headers=ADMIN
        )

        assert lowered.status_code == 200
        listed = (await api.get("/filesets", params={"name": "kept"})).json()["filesets"]
        assert [fileset["state"] for fileset in listed] == ["READY"], "no data is ever deleted"
        refused = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "more", "size_bytes": GIB}
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "ALLOCATION_LIMIT_EXCEEDED"

    async def test_a_limit_of_zero_stops_everything_new(self, api: httpx2.AsyncClient) -> None:
        await api.put(
            "/limits/mmustermann/LOC2HOT", json={"allocation_limit_bytes": 0}, headers=ADMIN
        )

        response = await api.post(
            "/filesets", json={"storage": "LOC2HOT", "name": "nope", "size_bytes": 1024}
        )

        assert response.status_code == 409

    async def test_a_negative_limit_is_a_usage_error(self, api: httpx2.AsyncClient) -> None:
        response = await api.put(
            "/limits/mmustermann/LOC2HOT", json={"allocation_limit_bytes": -1}, headers=ADMIN
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"
