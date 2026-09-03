"""Keeping the cluster config current while the process runs."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from stashd.api.app import create_app, refresh_cluster_config, reload_cluster_config
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ConfigDocument, load_cluster_document
from stashd.config.distribution import ConfigCache, ConfigUnavailable, HeldConfig, RefreshPlan
from tests.conftest import WriteConfig


class FakeSource:
    def __init__(self, answer: ConfigDocument | Exception) -> None:
        self.answer = answer

    def fetch(self) -> ConfigDocument:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def daemon_app(
    storage_bootstrap: BootstrapConfig, document: ConfigDocument, plan: RefreshPlan
) -> FastAPI:
    return create_app(storage_bootstrap, document.config, document=document, refresh=plan)


class TestRefresh:
    def _plan(
        self, document: ConfigDocument, answer: ConfigDocument | Exception, tmp_path: Path
    ) -> tuple[RefreshPlan, HeldConfig]:
        from datetime import timedelta

        held = HeldConfig(document=document, degraded=False)
        plan = RefreshPlan(
            source=FakeSource(answer),
            cache=ConfigCache(tmp_path / "cluster.yaml"),
            held=held,
            interval=timedelta(seconds=60),
        )
        return plan, held

    async def test_a_new_revision_is_picked_up_by_later_requests(
        self,
        storage_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        running = load_cluster_document(write_cluster(valid_cluster))
        valid_cluster["revision"] = 43
        plan, _ = self._plan(
            running, load_cluster_document(write_cluster(valid_cluster)), tmp_path
        )
        app = daemon_app(storage_bootstrap, running, plan)

        assert await refresh_cluster_config(app) is True
        assert app.state.cluster.revision == 43
        assert app.state.document.revision == 43

    async def test_a_refresh_that_fails_keeps_the_config_and_degrades(
        self,
        storage_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        running = load_cluster_document(write_cluster(valid_cluster))
        plan, _ = self._plan(running, ConfigUnavailable("controller down"), tmp_path)
        app = daemon_app(storage_bootstrap, running, plan)

        assert await refresh_cluster_config(app) is False
        assert app.state.cluster.revision == 42
        assert app.state.config_degraded is True

    async def test_a_process_with_nothing_to_refresh_does_nothing(
        self, storage_bootstrap: BootstrapConfig
    ) -> None:
        assert await refresh_cluster_config(create_app(storage_bootstrap)) is False


class TestReload:
    def _controller(
        self, controller_bootstrap: BootstrapConfig, document: ConfigDocument, path: Path
    ) -> FastAPI:
        return create_app(
            controller_bootstrap, document.config, document=document, reload_from=path
        )

    def test_a_new_revision_replaces_the_running_config(
        self,
        controller_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
    ) -> None:
        path = write_cluster(valid_cluster)
        app = self._controller(controller_bootstrap, load_cluster_document(path), path)
        valid_cluster["revision"] = 43
        write_cluster(valid_cluster)

        assert reload_cluster_config(app) is True
        assert app.state.cluster.revision == 43

    def test_a_config_that_does_not_validate_leaves_the_running_one_alone(
        self,
        controller_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
    ) -> None:
        path = write_cluster(valid_cluster)
        app = self._controller(controller_bootstrap, load_cluster_document(path), path)
        valid_cluster["revision"] = 43
        del valid_cluster["storages"][1]["capacity"]
        write_cluster(valid_cluster)

        assert reload_cluster_config(app) is False
        assert app.state.cluster.revision == 42
        assert app.state.cluster.storage("LOC2HOT").capacity_bytes is not None

    def test_an_unchanged_file_is_not_a_reload(
        self,
        controller_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
    ) -> None:
        path = write_cluster(valid_cluster)
        app = self._controller(controller_bootstrap, load_cluster_document(path), path)

        assert reload_cluster_config(app) is False

    def test_a_process_with_nothing_to_reload_does_nothing(
        self, storage_bootstrap: BootstrapConfig
    ) -> None:
        assert reload_cluster_config(create_app(storage_bootstrap)) is False


class TestAnnouncing:
    """A daemon says what it is on; the controller says whether that is still current."""

    class FakeRegistrar:
        def __init__(self, stale: bool = False) -> None:
            self.stale = stale
            self.calls: list[tuple[object, int]] = []

        def announce(self, announcement: object, config_revision: int) -> bool:
            self.calls.append((announcement, config_revision))
            return self.stale

    async def test_the_controller_can_say_the_daemon_is_behind(
        self,
        storage_bootstrap: BootstrapConfig,
        write_cluster: WriteConfig,
        valid_cluster: dict[str, Any],
    ) -> None:
        from stashd.api.app import announce

        document = load_cluster_document(write_cluster(valid_cluster))
        registrar = self.FakeRegistrar(stale=True)
        app = create_app(
            storage_bootstrap,
            document.config,
            document=document,
            registrar=registrar,
            announcement="hot1",
        )

        assert await announce(app) is True
        assert registrar.calls[0][1] == 42

    async def test_a_daemon_with_nothing_to_announce_to_says_nothing(
        self, storage_bootstrap: BootstrapConfig
    ) -> None:
        from stashd.api.app import announce

        assert await announce(create_app(storage_bootstrap)) is False
