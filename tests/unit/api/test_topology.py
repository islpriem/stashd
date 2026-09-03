"""Topology and identity endpoints, served from the cluster config."""

from fastapi.testclient import TestClient

from stashd import __version__


def test_whoami_reports_the_verified_identity(controller_app: TestClient) -> None:
    response = controller_app.get("/api/v1/whoami")

    assert response.status_code == 200
    assert response.json() == {
        "uid": 1000,
        "username": "mmustermann",
        "groups": ["users"],
        "admin": False,
        "server_version": __version__,
        "api_version": "v1",
    }


def test_whoami_marks_an_admin(controller_app: TestClient) -> None:
    response = controller_app.get(
        "/api/v1/whoami", headers={"Authorization": "Munge cred-admin"}
    )

    assert response.json()["admin"] is True


def test_locations_come_from_the_cluster_config(controller_app: TestClient) -> None:
    response = controller_app.get("/api/v1/locations")

    assert response.status_code == 200
    assert response.json() == {
        "locations": [
            {"id": "LOC1", "name": "Site 1", "enabled": True},
            {"id": "LOC2", "name": "Site 2", "enabled": True},
        ]
    }


def test_a_storage_daemon_serves_none_of_this(storage_bootstrap: object) -> None:
    from stashd.api.app import create_app

    client = TestClient(create_app(storage_bootstrap))  # type: ignore[arg-type]

    assert client.get("/api/v1/whoami").status_code == 404
    assert client.get("/api/v1/storages").status_code == 404
