"""Re-reading the cluster config on SIGHUP."""

from typing import Any

import pytest

from stashd.config.cluster import load_cluster_document
from stashd.config.errors import ConfigError
from stashd.config.reload import reload_document
from tests.conftest import WriteConfig


def test_a_new_revision_replaces_the_running_config(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    running = load_cluster_document(write_cluster(valid_cluster))
    valid_cluster["revision"] = 43
    valid_cluster["limits"]["max_filesets_per_user"] = 5
    path = write_cluster(valid_cluster)

    reloaded = reload_document(path, running)

    assert reloaded is not None
    assert reloaded.revision == 43
    assert reloaded.config.limits.max_filesets_per_user == 5


def test_an_unchanged_file_is_not_a_new_revision(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    path = write_cluster(valid_cluster)
    running = load_cluster_document(path)

    assert reload_document(path, running) is None


def test_changed_content_without_a_higher_revision_is_refused(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    """Two different configs may not share a revision: it is what identifies them."""
    running = load_cluster_document(write_cluster(valid_cluster))
    valid_cluster["limits"]["max_filesets_per_user"] = 5
    path = write_cluster(valid_cluster)

    with pytest.raises(ConfigError, match="revision 42"):
        reload_document(path, running)


def test_an_invalid_file_is_refused_and_the_running_config_stays(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    running = load_cluster_document(write_cluster(valid_cluster))
    valid_cluster["revision"] = 43
    del valid_cluster["storages"][1]["capacity"]
    path = write_cluster(valid_cluster)

    with pytest.raises(ConfigError, match="needs a capacity"):
        reload_document(path, running)

    assert running.revision == 42


def test_a_revision_that_went_backwards_is_refused(
    write_cluster: WriteConfig, valid_cluster: dict[str, Any]
) -> None:
    running = load_cluster_document(write_cluster(valid_cluster))
    valid_cluster["revision"] = 41
    path = write_cluster(valid_cluster)

    with pytest.raises(ConfigError, match="monotonic"):
        reload_document(path, running)
