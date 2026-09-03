"""How a storage daemon gets the cluster config, and keeps it."""

from pathlib import Path
from typing import Any

import pytest

from stashd.config.cluster import ConfigDocument, load_cluster_document
from stashd.config.distribution import (
    ConfigCache,
    ConfigUnavailable,
    obtain_config,
    refresh_config,
)
from stashd.config.errors import ConfigError
from tests.conftest import WriteConfig


class FakeSource:
    def __init__(self, *documents: ConfigDocument | Exception) -> None:
        self.answers = list(documents)
        self.calls = 0

    def fetch(self) -> ConfigDocument:
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def document(write_cluster: WriteConfig, valid_cluster: dict[str, Any]) -> ConfigDocument:
    return load_cluster_document(write_cluster(valid_cluster))


@pytest.fixture
def newer(write_cluster: WriteConfig, valid_cluster: dict[str, Any]) -> ConfigDocument:
    valid_cluster["revision"] = 43
    return load_cluster_document(write_cluster(valid_cluster))


@pytest.fixture
def cache(tmp_path: Path) -> ConfigCache:
    return ConfigCache(tmp_path / "var" / "cluster.yaml")


class TestObtaining:
    def test_the_config_is_fetched_and_cached(
        self, document: ConfigDocument, cache: ConfigCache
    ) -> None:
        held = obtain_config(FakeSource(document), cache)

        assert held.document.revision == 42
        assert not held.degraded
        assert cache.read() is not None
        assert cache.read().revision == 42  # type: ignore[union-attr]

    def test_an_unreachable_controller_starts_from_the_cache_degraded(
        self, document: ConfigDocument, cache: ConfigCache
    ) -> None:
        cache.write(document)

        held = obtain_config(FakeSource(ConfigUnavailable("connection refused")), cache)

        assert held.document.revision == 42
        assert held.degraded, "running from cache is degraded"

    def test_without_a_cache_an_unreachable_controller_stops_the_daemon(
        self, cache: ConfigCache
    ) -> None:
        with pytest.raises(ConfigError, match="no cached cluster config"):
            obtain_config(FakeSource(ConfigUnavailable("connection refused")), cache)

    def test_a_cache_that_no_longer_validates_is_not_used(
        self, cache: ConfigCache, tmp_path: Path
    ) -> None:
        cache.path.parent.mkdir(parents=True, exist_ok=True)
        cache.path.write_text("revision: -1\n")

        with pytest.raises(ConfigError):
            obtain_config(FakeSource(ConfigUnavailable("down")), cache)

    def test_a_served_config_that_does_not_validate_is_refused(
        self, cache: ConfigCache
    ) -> None:
        with pytest.raises(ConfigError):
            obtain_config(FakeSource(ConfigError(Path("<served>"), ["nonsense"])), cache)


class TestRefreshing:
    def test_a_new_revision_replaces_the_current_one_and_the_cache(
        self, document: ConfigDocument, newer: ConfigDocument, cache: ConfigCache
    ) -> None:
        held = obtain_config(FakeSource(document), cache)

        assert refresh_config(FakeSource(newer), cache, held) is True
        assert held.document.revision == 43
        assert cache.read().revision == 43  # type: ignore[union-attr]

    def test_the_same_revision_changes_nothing(
        self, document: ConfigDocument, cache: ConfigCache
    ) -> None:
        held = obtain_config(FakeSource(document), cache)

        assert refresh_config(FakeSource(document), cache, held) is False
        assert held.document.revision == 42

    def test_a_refresh_that_fails_keeps_what_the_daemon_has_and_degrades_it(
        self, document: ConfigDocument, cache: ConfigCache
    ) -> None:
        held = obtain_config(FakeSource(document), cache)

        assert refresh_config(FakeSource(ConfigUnavailable("gone")), cache, held) is False
        assert held.document.revision == 42
        assert held.degraded

    def test_a_successful_refresh_clears_the_degradation(
        self, document: ConfigDocument, newer: ConfigDocument, cache: ConfigCache
    ) -> None:
        cache.write(document)
        held = obtain_config(FakeSource(ConfigUnavailable("down")), cache)
        assert held.degraded

        refresh_config(FakeSource(newer), cache, held)

        assert not held.degraded

    def test_a_served_config_that_does_not_validate_never_replaces_a_good_one(
        self, document: ConfigDocument, cache: ConfigCache
    ) -> None:
        held = obtain_config(FakeSource(document), cache)

        assert refresh_config(FakeSource(ConfigError(Path("x"), ["bad"])), cache, held) is False
        assert held.document.revision == 42
