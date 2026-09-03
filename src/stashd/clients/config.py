"""Fetching the cluster config from the controller."""

import httpx2

from stashd.config.cluster import ConfigDocument, parse_cluster_document
from stashd.config.distribution import ConfigUnavailable

CONFIG_PATH = "/internal/v1/cluster-config"


class HttpConfigSource:
    def __init__(self, controller_url: str, token: str, timeout: float = 10.0) -> None:
        self._url = controller_url.rstrip("/") + CONFIG_PATH
        self._token = token
        self._timeout = timeout

    def fetch(self) -> ConfigDocument:
        try:
            response = httpx2.get(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx2.HTTPError as error:
            raise ConfigUnavailable(f"{self._url}: {error}") from error
        if not response.is_success:
            raise ConfigUnavailable(f"{self._url} answered {response.status_code}")
        return parse_cluster_document(response.json()["text"])
