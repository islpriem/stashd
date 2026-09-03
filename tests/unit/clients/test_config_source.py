"""The daemon's fetch of the cluster config."""

from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from stashd.clients.config import HttpConfigSource
from stashd.config.cluster import ConfigDocument, load_cluster_document
from stashd.config.distribution import ConfigUnavailable
from stashd.config.errors import ConfigError
from tests.conftest import WriteConfig


@pytest.fixture
def document(write_cluster: WriteConfig, valid_cluster: dict[str, object]) -> ConfigDocument:
    return load_cluster_document(write_cluster(valid_cluster))


Handler = Callable[[str, dict[str, Any]], httpx2.Response]


def source(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> HttpConfigSource:
    def get(url: str, **kwargs: Any) -> httpx2.Response:
        return handler(url, kwargs)

    monkeypatch.setattr(httpx2, "get", get)
    return HttpConfigSource("http://controller:8000", "peer-token")


def test_the_config_is_fetched_with_the_peer_token(
    monkeypatch: pytest.MonkeyPatch, document: ConfigDocument
) -> None:
    seen: list[tuple[str, object]] = []

    def handler(url: str, kwargs: dict[str, object]) -> httpx2.Response:
        seen.append((url, kwargs["headers"]))
        return httpx2.Response(
            200,
            json={"revision": 42, "content_hash": document.content_hash, "text": document.text},
        )

    fetched = source(monkeypatch, handler).fetch()

    assert seen[0][0] == "http://controller:8000/internal/v1/cluster-config"
    assert seen[0][1] == {"Authorization": "Bearer peer-token"}
    assert fetched.config == document.config


def test_a_controller_that_cannot_be_reached_is_unavailable_not_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(url: str, kwargs: dict[str, object]) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    with pytest.raises(ConfigUnavailable):
        source(monkeypatch, handler).fetch()


def test_a_refusal_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, kwargs: dict[str, object]) -> httpx2.Response:
        return httpx2.Response(401, json={})

    with pytest.raises(ConfigUnavailable):
        source(monkeypatch, handler).fetch()


def test_a_config_that_does_not_validate_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(url: str, kwargs: dict[str, object]) -> httpx2.Response:
        return httpx2.Response(
            200, json={"revision": 1, "content_hash": "x", "text": "nonsense: 1"}
        )

    with pytest.raises(ConfigError):
        source(monkeypatch, handler).fetch()
