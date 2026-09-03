"""Announcing this daemon to the controller."""

from dataclasses import dataclass

import httpx2
import structlog

logger = structlog.get_logger()
REGISTER_PATH = "/internal/v1/register"


@dataclass(frozen=True, slots=True)
class Announcement:
    daemon_id: str
    storages: list[str]
    version: str


class HttpRegistrar:
    def __init__(self, controller_url: str, token: str, timeout: float = 10.0) -> None:
        self._url = controller_url.rstrip("/") + REGISTER_PATH
        self._token = token
        self._timeout = timeout

    def announce(self, announcement: Announcement, config_revision: int) -> bool:
        """True when the controller says this daemon should fetch the config again."""
        try:
            response = httpx2.post(
                self._url,
                json={
                    "daemon_id": announcement.daemon_id,
                    "storages": announcement.storages,
                    "config_revision": config_revision,
                    "version": announcement.version,
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx2.HTTPError as error:
            logger.warning("register.failed", reason=str(error))
            return False
        if not response.is_success:
            logger.warning("register.refused", status=response.status_code, body=response.text)
            return False
        return bool(response.json().get("refresh_needed"))
