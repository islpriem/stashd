"""Asking daemons to prepare a destination and to move the data."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from stashd.clients.filesets import DaemonUnavailable, failure_from
from stashd.config.cluster import ClusterConfig
from stashd.domain.storage import Owner
from stashd.engines.base import TransferEndpoint

INTERNAL_PREFIX = "/internal/v1"


@dataclass(frozen=True, slots=True)
class TaskState:
    """What a daemon says a task is doing now."""

    state: str
    bytes_done: int = 0
    files_done: int = 0
    failure: str | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class StartedTask:
    task_id: str
    daemon_id: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    # Where the daemon says that path really is, inside its storage.
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    bytes_total: int
    file_count: int
    complete: bool
    writable: bool = False


class TransferDispatcher(Protocol):
    async def task_state(self, storage_id: str, task_id: str) -> TaskState: ...

    async def abort(self, storage_id: str, task_id: str) -> None: ...

    async def probe(self, storage_id: str, owner: Owner, path: str) -> ProbeResult: ...

    async def prepare(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> TransferEndpoint: ...

    async def start(
        self,
        storage_id: str,
        *,
        transfer_id: int,
        source_path: str,
        owner: Owner,
        target: TransferEndpoint,
        bwlimit_bytes_per_s: int | None,
        delete: bool,
    ) -> StartedTask: ...


class HttpTransferDispatcher:
    def __init__(self, cluster: ClusterConfig, token: str, client: httpx2.AsyncClient) -> None:
        self._cluster = cluster
        self._token = token
        self._client = client

    async def probe(self, storage_id: str, owner: Owner, path: str) -> ProbeResult:
        body = await self._post(
            storage_id,
            "/probe",
            {"storage_id": storage_id, "path": path, "owner": _owner(owner)},
        )
        return ProbeResult(
            exists=bool(body["exists"]),
            is_dir=bool(body["is_dir"]),
            path=str(body["path"]),
            readable=bool(body["readable"]),
            writable=bool(body.get("writable", False)),
            bytes_total=int(body["bytes_total"]),
            file_count=int(body["file_count"]),
            complete=bool(body["complete"]),
        )

    async def prepare(
        self, storage_id: str, owner: Owner, name: str, allocation_bytes: int
    ) -> TransferEndpoint:
        body = await self._post(
            storage_id,
            "/prepare",
            {
                "storage_id": storage_id,
                "name": name,
                "owner": _owner(owner),
                "allocation_bytes": allocation_bytes,
            },
        )
        return TransferEndpoint(
            path=str(body["path"]), host=body.get("host"), user=body.get("user")
        )

    async def start(
        self,
        storage_id: str,
        *,
        transfer_id: int,
        source_path: str,
        owner: Owner,
        target: TransferEndpoint,
        bwlimit_bytes_per_s: int | None,
        delete: bool,
    ) -> StartedTask:
        body = await self._post(
            storage_id,
            "/tasks",
            {
                "transfer_id": transfer_id,
                "storage_id": storage_id,
                "source_path": source_path,
                "owner": _owner(owner),
                "target": {"path": target.path, "host": target.host, "user": target.user},
                "bwlimit_bytes_per_s": bwlimit_bytes_per_s,
                "delete": delete,
            },
        )
        return StartedTask(
            task_id=str(body["task_id"]), daemon_id=self._cluster.daemon_for(storage_id).id
        )

    async def task_state(self, storage_id: str, task_id: str) -> TaskState:
        daemon = self._cluster.daemon_for(storage_id)
        body = await self._request(
            daemon.id, storage_id, "GET", f"{daemon.url}{INTERNAL_PREFIX}/tasks/{task_id}", None
        )
        return TaskState(
            state=str(body["state"]),
            bytes_done=int(body.get("bytes_done", 0)),
            files_done=int(body.get("files_done", 0)),
            failure=body.get("failure"),
            message=str(body.get("message", "")),
        )

    async def abort(self, storage_id: str, task_id: str) -> None:
        """Kill it where it runs. The partial data stays where it is."""
        daemon = self._cluster.daemon_for(storage_id)
        await self._request(
            daemon.id,
            storage_id,
            "DELETE",
            f"{daemon.url}{INTERNAL_PREFIX}/tasks/{task_id}",
            None,
        )

    async def _post(self, storage_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        daemon = self._cluster.daemon_for(storage_id)
        return await self._request(
            daemon.id, storage_id, "POST", f"{daemon.url}{INTERNAL_PREFIX}{path}", body
        )

    async def _request(
        self,
        daemon_id: str,
        storage_id: str,
        method: str,
        url: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, url, json=body, headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx2.HTTPError as error:
            raise DaemonUnavailable(
                f"daemon {daemon_id} for {storage_id} is unreachable: {error}",
                daemon=daemon_id,
                storage=storage_id,
            ) from error
        if response.is_success:
            return dict(response.json())
        raise failure_from(response, daemon_id, storage_id)


def _owner(owner: Owner) -> dict[str, Any]:
    return {"user": owner.user, "uid": owner.uid, "gid": owner.gid}
