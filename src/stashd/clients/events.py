"""Telling the controller what a transfer is doing.

Events carry a sequence number the controller orders them by, so a lost or repeated
report cannot corrupt what it believes.
"""

from datetime import UTC, datetime
from itertools import count
from threading import Lock
from typing import Protocol

import httpx2
import structlog

logger = structlog.get_logger()
EVENTS_PATH = "/internal/v1/events"


class EventReporter(Protocol):
    def report(self, transfer_id: int, kind: str, **fields: object) -> None: ...


class HttpEventReporter:
    def __init__(self, controller_url: str, token: str, daemon_id: str, timeout: float = 10.0):
        self._url = controller_url.rstrip("/") + EVENTS_PATH
        self._token = token
        self._daemon_id = daemon_id
        self._timeout = timeout
        self._sequences: dict[int, count[int]] = {}
        self._lock = Lock()

    def report(self, transfer_id: int, kind: str, **fields: object) -> None:
        with self._lock:
            sequence = next(self._sequences.setdefault(transfer_id, count(1)))
        body = {
            "transfer_id": transfer_id,
            "sequence": sequence,
            "kind": kind,
            "daemon_id": self._daemon_id,
            "at": datetime.now(UTC).isoformat(),
            **fields,
        }
        try:
            httpx2.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
        except httpx2.HTTPError as error:
            # The controller reconciles against the daemon on startup, so a lost
            # event delays the truth rather than losing it.
            logger.warning("event.not_delivered", transfer_id=transfer_id, reason=str(error))


class NullEventReporter:
    def report(self, transfer_id: int, kind: str, **fields: object) -> None:
        """Nothing to tell: what a daemon without a controller uses."""
