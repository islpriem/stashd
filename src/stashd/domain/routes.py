"""Routes, transfer channels and the best-effort ETA."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class Channel(StrEnum):
    LOCAL = "local"
    SSH = "ssh"


@dataclass(frozen=True, slots=True)
class Route:
    """The ordered pair of storages a transfer moves between; the unit of limiting."""

    source: str
    target: str

    def __str__(self) -> str:
        return f"{self.source}->{self.target}"


def channel_for(source_daemon: str, target_daemon: str) -> Channel:
    """One daemon for both ends means plain local rsync; otherwise rsync over SSH."""
    return Channel.LOCAL if source_daemon == target_daemon else Channel.SSH


def throughput_for(route: Route, *, default: int, routes: Mapping[str, int]) -> int:
    return routes.get(str(route), default)


def estimate(
    *, bytes_total: int, queued_ahead: int, throughput: int
) -> tuple[timedelta, timedelta]:
    """Best-effort (start delay, duration) from nominal throughput; never a promise."""
    return (
        timedelta(seconds=math.ceil(queued_ahead / throughput)),
        timedelta(seconds=math.ceil(bytes_total / throughput)),
    )
