"""Role-dependent app assembly and health endpoints."""

import pytest
from fastapi.testclient import TestClient

from stashd import __version__
from stashd.api.app import create_app
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig


@pytest.fixture
def controller(
    controller_bootstrap: BootstrapConfig, cluster_config: ClusterConfig
) -> TestClient:
    return TestClient(create_app(controller_bootstrap, cluster_config))


@pytest.fixture
def daemon(storage_bootstrap: BootstrapConfig) -> TestClient:
    return TestClient(create_app(storage_bootstrap))


def test_controller_healthz(controller: TestClient) -> None:
    response = controller.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "role": "controller",
        "daemon_id": "controller",
        "version": __version__,
    }


def test_storage_daemon_healthz(daemon: TestClient) -> None:
    response = daemon.get("/healthz")

    assert response.status_code == 200
    assert response.json()["role"] == "storage"
    assert response.json()["daemon_id"] == "hot1"


def test_controller_readyz_reports_its_config_revision(controller: TestClient) -> None:
    response = controller.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "degraded": False,
        "config_revision": 42,
        "checks": {},
    }


def test_storage_daemon_is_not_ready_without_a_cluster_config(daemon: TestClient) -> None:
    response = daemon.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "degraded": True,
        "config_revision": None,
        "checks": {"cluster_config": "not fetched"},
    }


def test_storage_daemon_does_not_serve_the_public_api(
    storage_bootstrap: BootstrapConfig,
) -> None:
    app = create_app(storage_bootstrap)

    assert not [route for route in app.routes if "/api/v1" in getattr(route, "path", "")]
    assert TestClient(app).get("/api/v1/filesets").status_code == 404


def test_a_daemon_on_a_cached_config_reports_itself_degraded(
    storage_bootstrap: BootstrapConfig, cluster_config: ClusterConfig
) -> None:
    """It can work, so it is ready; it is not running on what the controller has."""
    client = TestClient(create_app(storage_bootstrap, cluster_config, degraded=True))

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded"] is True
    assert "cache" in body["checks"]["cluster_config"]
