"""The controller's client for a storage daemon."""

from typing import Any

import httpx2
import pytest

from stashd.clients.filesets import DaemonUnavailable, HttpFilesetStore, RemoteFailure
from stashd.config.cluster import ClusterConfig
from stashd.domain.errors import ErrorCode
from stashd.domain.storage import FilesetLocation, Owner

OWNER = Owner(user="mmustermann", uid=1000, gid=1000)


def store(cluster_config: ClusterConfig, handler: Any) -> HttpFilesetStore:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return HttpFilesetStore(cluster_config, "peer-token", client)


async def test_create_goes_to_the_daemon_that_serves_the_storage(
    cluster_config: ClusterConfig,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            201, json={"storage_id": "LOC2HOT", "name": "mydir", "path": "/p"}
        )

    location = await store(cluster_config, handler).create("LOC2HOT", OWNER, "mydir", 1024)

    assert str(seen[0].url) == "http://127.0.0.1:8002/internal/v1/filesets"
    assert seen[0].headers["Authorization"] == "Bearer peer-token"
    assert location.path == "/p"
    assert location.owner == OWNER


async def test_delete_names_the_fileset_id_and_the_location(
    cluster_config: ClusterConfig,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(204)

    location = FilesetLocation(storage_id="LOC2HOT", name="mydir", owner=OWNER, path="/p")

    await store(cluster_config, handler).delete(42, location)

    assert seen[0].url.path == "/internal/v1/filesets/42"
    assert seen[0].method == "DELETE"


async def test_a_refusal_keeps_the_daemons_code_and_numbers(
    cluster_config: ClusterConfig,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400,
            json={
                "error": {
                    "code": "INVALID_PATH",
                    "message": "/etc is not a fileset",
                    "details": {"path": "/etc"},
                    "request_id": "01J",
                }
            },
        )

    with pytest.raises(RemoteFailure) as excinfo:
        await store(cluster_config, handler).create("LOC2HOT", OWNER, "mydir", 1024)

    assert excinfo.value.code is ErrorCode.INVALID_PATH
    assert excinfo.value.details == {"path": "/etc"}


async def test_a_daemon_that_cannot_be_reached_is_reported_as_such(
    cluster_config: ClusterConfig,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(DaemonUnavailable) as excinfo:
        await store(cluster_config, handler).create("LOC2HOT", OWNER, "mydir", 1024)

    assert excinfo.value.details["daemon"] == "loc2hot"


async def test_a_body_that_is_not_an_envelope_is_still_a_failure(
    cluster_config: ClusterConfig,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, text="<html>gateway</html>")

    with pytest.raises(DaemonUnavailable):
        await store(cluster_config, handler).create("LOC2HOT", OWNER, "mydir", 1024)


async def test_an_unknown_error_code_becomes_internal(cluster_config: ClusterConfig) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            500,
            json={"error": {"code": "WHAT", "message": "?", "details": {}, "request_id": "1"}},
        )

    with pytest.raises(RemoteFailure) as excinfo:
        await store(cluster_config, handler).create("LOC2HOT", OWNER, "mydir", 1024)

    assert excinfo.value.code is ErrorCode.INTERNAL
