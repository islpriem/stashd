"""The controller's way to reach the filesystem: ask the daemon that owns it.

The controller never touches user data, so creating and deleting a
fileset directory is a request to the storage daemon, authenticated with a peer token.
"""

from typing import Any, Protocol

import httpx2

from stashd.config.cluster import ClusterConfig
from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.storage import FilesetLocation, Owner

INTERNAL_PREFIX = "/internal/v1"


class DaemonUnavailable(StashError):
    code = ErrorCode.DAEMON_UNAVAILABLE


class RemoteFailure(StashError):
    """What the daemon refused, re-raised with the daemon's own code and numbers."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any]) -> None:
        self.code = code
        super().__init__(message, **details)


class FilesetStore(Protocol):
    async def create(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> FilesetLocation: ...

    async def delete(self, fileset_id: int, location: FilesetLocation) -> None: ...


class HttpFilesetStore:
    def __init__(self, cluster: ClusterConfig, token: str, client: httpx2.AsyncClient) -> None:
        self._cluster = cluster
        self._token = token
        self._client = client

    async def create(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> FilesetLocation:
        body = await self._request(
            storage_id,
            "POST",
            "/filesets",
            {
                "storage_id": storage_id,
                "name": name,
                "owner": {"user": owner.user, "uid": owner.uid, "gid": owner.gid},
                "allocation_bytes": allocation_bytes,
            },
        )
        return FilesetLocation(
            storage_id=storage_id, name=name, owner=owner, path=str(body["path"])
        )

    async def delete(self, fileset_id: int, location: FilesetLocation) -> None:
        owner = location.owner
        await self._request(
            location.storage_id,
            "DELETE",
            f"/filesets/{fileset_id}",
            {
                "storage_id": location.storage_id,
                "name": location.name,
                "owner": {"user": owner.user, "uid": owner.uid, "gid": owner.gid},
                "path": location.path,
            },
        )

    async def _request(
        self, storage_id: str, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        daemon = self._cluster.daemon_for(storage_id)
        try:
            response = await self._client.request(
                method,
                f"{daemon.url}{INTERNAL_PREFIX}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx2.HTTPError as error:
            raise DaemonUnavailable(
                f"daemon {daemon.id} for {storage_id} is unreachable: {error}",
                daemon=daemon.id,
                storage=storage_id,
            ) from error
        if response.is_success:
            return dict(response.json()) if response.content else {}
        raise _failure(response, daemon.id, storage_id)


def _failure(response: httpx2.Response, daemon_id: str, storage_id: str) -> StashError:
    try:
        error = dict(response.json()["error"])
    except (ValueError, KeyError, TypeError):
        return DaemonUnavailable(
            f"daemon {daemon_id} answered {response.status_code} for {storage_id}",
            daemon=daemon_id,
            storage=storage_id,
        )
    try:
        code = ErrorCode(str(error.get("code")))
    except ValueError:
        code = ErrorCode.INTERNAL
    return RemoteFailure(code, str(error.get("message", "")), dict(error.get("details") or {}))
