"""Asking a daemon what its filesets actually hold."""

from dataclasses import dataclass
from typing import Protocol

import httpx2

from stashd.clients.filesets import DaemonUnavailable
from stashd.config.cluster import ClusterConfig
from stashd.domain.storage import Owner


@dataclass(frozen=True, slots=True)
class Measurable:
    """One fileset the controller wants measured, as the daemon needs to address it."""

    fileset_id: int
    name: str
    owner: Owner
    path: str


@dataclass(frozen=True, slots=True)
class Measured:
    used_bytes: int
    file_count: int


class UsageSource(Protocol):
    async def fileset_usage(
        self, storage_id: str, locations: list[Measurable]
    ) -> dict[int, Measured]: ...


class HttpUsageSource:
    """The daemon that owns the storage does the measuring; it is the only one that can."""

    def __init__(self, cluster: ClusterConfig, token: str, client: httpx2.AsyncClient) -> None:
        self._cluster = cluster
        self._token = token
        self._client = client

    async def fileset_usage(
        self, storage_id: str, locations: list[Measurable]
    ) -> dict[int, Measured]:
        daemon = self._cluster.daemon_for(storage_id)
        try:
            response = await self._client.post(
                f"{daemon.url}/internal/v1/filesets/usage",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "storage_id": storage_id,
                    "filesets": [
                        {
                            "fileset_id": location.fileset_id,
                            "name": location.name,
                            "owner": {
                                "user": location.owner.user,
                                "uid": location.owner.uid,
                                "gid": location.owner.gid,
                            },
                            "path": location.path,
                        }
                        for location in locations
                    ],
                },
            )
            response.raise_for_status()
        except httpx2.HTTPError as error:
            raise DaemonUnavailable(
                f"{daemon.id} for {storage_id} is unreachable: {error}", daemon=daemon.id
            ) from error
        return {
            int(row["fileset_id"]): Measured(
                used_bytes=int(row["used_bytes"]), file_count=int(row["file_count"])
            )
            for row in response.json()["filesets"]
        }
