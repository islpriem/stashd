"""The internal API on a storage daemon."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stashd.api.app import create_app
from stashd.auth.token import TokenAuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.drivers.posix import PosixDriver
from stashd.identity.current import CurrentUserIdentity

TOKEN = "peer-s3cret"
OWNER = {"user": "tester", "uid": os.getuid(), "gid": os.getgid()}


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    root = tmp_path / "cache"
    root.mkdir()
    return root


@pytest.fixture
def daemon(storage_bootstrap: BootstrapConfig, prefix: Path) -> TestClient:
    driver = PosixDriver("LOC2HOT", fileset_prefix=prefix, identity=CurrentUserIdentity())
    app = create_app(
        storage_bootstrap,
        peer_auth=TokenAuthProvider(TOKEN),
        drivers={"LOC2HOT": driver},
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def create_body(name: str = "mydir") -> dict[str, object]:
    return {"storage_id": "LOC2HOT", "name": name, "owner": OWNER, "allocation_bytes": 1024}


class TestAuthentication:
    def test_no_token_is_refused(self, daemon: TestClient) -> None:
        response = daemon.post(
            "/internal/v1/filesets", json=create_body(), headers={"Authorization": ""}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_a_wrong_token_is_refused(self, daemon: TestClient) -> None:
        response = daemon.post(
            "/internal/v1/filesets",
            json=create_body(),
            headers={"Authorization": "Bearer guess"},
        )

        assert response.status_code == 401

    def test_a_munge_credential_does_not_open_the_internal_api(
        self, daemon: TestClient
    ) -> None:
        response = daemon.post(
            "/internal/v1/filesets",
            json=create_body(),
            headers={"Authorization": f"Munge {TOKEN}"},
        )

        assert response.status_code == 401

    def test_the_token_is_never_echoed(self, daemon: TestClient) -> None:
        response = daemon.post(
            "/internal/v1/filesets",
            json=create_body(),
            headers={"Authorization": "Bearer guess"},
        )

        assert TOKEN not in response.text


class TestCreate:
    def test_the_directory_is_made_and_its_path_returned(
        self, daemon: TestClient, prefix: Path
    ) -> None:
        response = daemon.post("/internal/v1/filesets", json=create_body())

        assert response.status_code == 201
        body = response.json()
        assert body == {
            "storage_id": "LOC2HOT",
            "name": "mydir",
            "path": str(prefix / "tester" / "mydir"),
        }
        assert (prefix / "tester" / "mydir").is_dir()

    def test_creating_twice_is_not_an_error(self, daemon: TestClient) -> None:
        daemon.post("/internal/v1/filesets", json=create_body())

        assert daemon.post("/internal/v1/filesets", json=create_body()).status_code == 201

    def test_a_storage_this_daemon_does_not_serve_is_not_found(
        self, daemon: TestClient
    ) -> None:
        response = daemon.post(
            "/internal/v1/filesets", json={**create_body(), "storage_id": "ELSEWHERE"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_a_name_that_is_not_a_fileset_name_is_refused(self, daemon: TestClient) -> None:
        response = daemon.post("/internal/v1/filesets", json=create_body("../escape"))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_NAME"


class TestDelete:
    def test_the_directory_and_its_contents_go(self, daemon: TestClient, prefix: Path) -> None:
        path = daemon.post("/internal/v1/filesets", json=create_body()).json()["path"]
        (Path(path) / "payload").write_text("data")

        response = daemon.request(
            "DELETE",
            "/internal/v1/filesets/42",
            json={"storage_id": "LOC2HOT", "name": "mydir", "owner": OWNER, "path": path},
        )

        assert response.status_code == 204
        assert not Path(path).exists()

    def test_deleting_what_is_already_gone_succeeds(
        self, daemon: TestClient, prefix: Path
    ) -> None:
        path = str(prefix / "tester" / "never-existed")

        response = daemon.request(
            "DELETE",
            "/internal/v1/filesets/42",
            json={
                "storage_id": "LOC2HOT",
                "name": "never-existed",
                "owner": OWNER,
                "path": path,
            },
        )

        assert response.status_code == 204

    def test_a_path_outside_the_storage_is_refused(self, daemon: TestClient) -> None:
        response = daemon.request(
            "DELETE",
            "/internal/v1/filesets/42",
            json={
                "storage_id": "LOC2HOT",
                "name": "mydir",
                "owner": OWNER,
                "path": "/etc/stash",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PATH"


def test_a_controller_serves_the_internal_api_too(
    controller_bootstrap: BootstrapConfig, cluster_config: object
) -> None:
    app = create_app(controller_bootstrap, cluster_config, peer_auth=TokenAuthProvider(TOKEN))  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        "/internal/v1/filesets",
        json=create_body(),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 404, "the controller serves no storage"
