"""Getting the cluster config to a storage daemon and keeping it there.

A daemon fetches at startup and caches what it got. If the controller is unreachable it
starts from the cache and says so; without a cache it refuses to start. A config that does
not validate is never used, wherever it came from.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import structlog

from stashd.config.cluster import ConfigDocument, parse_cluster_document
from stashd.config.errors import ConfigError

logger = structlog.get_logger()


class ConfigUnavailable(Exception):
    """The controller could not be asked. Its config may still be perfectly good."""


class ConfigSource(Protocol):
    def fetch(self) -> ConfigDocument: ...


@dataclass
class ConfigCache:
    path: Path

    def read(self) -> ConfigDocument | None:
        if not self.path.is_file():
            return None
        return parse_cluster_document(self.path.read_text(), self.path)

    def write(self, document: ConfigDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(document.text)


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """What a daemon needs to keep its config current."""

    source: "ConfigSource"
    cache: ConfigCache
    held: "HeldConfig"
    interval: timedelta


@dataclass
class HeldConfig:
    """What the daemon is running on, and whether it reached the controller for it."""

    document: ConfigDocument
    degraded: bool


def obtain_config(source: ConfigSource, cache: ConfigCache) -> HeldConfig:
    try:
        document = source.fetch()
    except ConfigUnavailable as unavailable:
        cached = cache.read()
        if cached is None:
            raise ConfigError(
                cache.path,
                [
                    f"the controller is unreachable ({unavailable}) and there is no cached "
                    f"cluster config at {cache.path}"
                ],
            ) from None
        logger.warning("config.from_cache", reason=str(unavailable), revision=cached.revision)
        return HeldConfig(document=cached, degraded=True)
    cache.write(document)
    return HeldConfig(document=document, degraded=False)


def refresh_config(source: ConfigSource, cache: ConfigCache, held: HeldConfig) -> bool:
    """Take a newer config if there is one. A failure never discards what works."""
    try:
        document = source.fetch()
    except (ConfigUnavailable, ConfigError) as problem:
        logger.warning("config.refresh_failed", reason=str(problem))
        held.degraded = True
        return False
    held.degraded = False
    if document.content_hash == held.document.content_hash:
        return False
    cache.write(document)
    held.document = document
    logger.info("config.updated", revision=document.revision)
    return True
