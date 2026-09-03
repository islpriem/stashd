"""The cluster config as something that can be served and cached."""

from pathlib import Path
from typing import Any

import pytest

from stashd.config.cluster import load_cluster_document, parse_cluster_document
from stashd.config.errors import ConfigError
from tests.conftest import WriteConfig


def test_a_document_carries_the_text_that_was_loaded(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    path = write_cluster(valid_cluster)

    document = load_cluster_document(path)

    assert document.revision == 42
    assert document.text == path.read_text()
    assert document.config.storage("LOC2HOT").capacity_bytes == 500 * 1024**4


def test_a_document_parses_back_from_its_text(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    original = load_cluster_document(write_cluster(valid_cluster))

    received = parse_cluster_document(original.text)

    assert received.revision == original.revision
    assert received.content_hash == original.content_hash
    assert received.config == original.config


def test_the_hash_is_the_config_not_the_formatting(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    original = load_cluster_document(write_cluster(valid_cluster))

    commented = parse_cluster_document(original.text + "\n# a comment\n")

    assert commented.content_hash == original.content_hash
    assert commented.text != original.text


def test_a_document_that_does_not_validate_is_refused() -> None:
    with pytest.raises(ConfigError, match="revision"):
        parse_cluster_document("locations: []\n")


def test_text_that_is_not_yaml_is_refused() -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse_cluster_document("revision: [unclosed\n")


def test_a_missing_file_is_reported_with_its_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_cluster_document(tmp_path / "absent.yaml")
