"""Probe, prepare and run, on the daemon that owns the storage."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stashd.api.app import create_app
from stashd.auth.token import TokenAuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.drivers.posix import PosixDriver
from stashd.engines.fake import FakeEngine
from stashd.identity.current import CurrentUserIdentity
from stashd.tasks.inline import InlineTaskRunner

TOKEN = "peer-s3cret"
OWNER = {"user": "tester", "uid": os.getuid(), "gid": os.getgid()}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    storage = tmp_path / "storage"
    storage.mkdir()
    return storage


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(bytes_transferred=4096, files_transferred=2)


@pytest.fixture
def daemon(storage_bootstrap: BootstrapConfig, root: Path, engine: FakeEngine) -> TestClient:
    driver = PosixDriver("HOT1", fileset_prefix=root, identity=CurrentUserIdentity(), root=root)
    app = create_app(
        storage_bootstrap,
        peer_auth=TokenAuthProvider(TOKEN),
        drivers={"HOT1": driver},
        runner=InlineTaskRunner({"HOT1": driver}, engine),
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


class TestProbe:
    def test_a_readable_directory_is_measured(self, daemon: TestClient, root: Path) -> None:
        (root / "myuser").mkdir()
        (root / "myuser" / "one").write_bytes(b"x" * 4096)

        response = daemon.post(
            "/internal/v1/probe",
            json={"storage_id": "HOT1", "path": "/myuser", "owner": OWNER},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["exists"] and body["is_dir"] and body["readable"]
        assert body["file_count"] == 1
        assert body["bytes_total"] >= 4096
        assert body["path"] == str((root / "myuser").resolve())

    def test_something_that_is_not_there_is_reported_as_absent(
        self, daemon: TestClient
    ) -> None:
        response = daemon.post(
            "/internal/v1/probe",
            json={"storage_id": "HOT1", "path": "/nothing", "owner": OWNER},
        )

        assert response.status_code == 200
        assert response.json()["exists"] is False

    def test_a_path_that_leaves_the_storage_is_refused(self, daemon: TestClient) -> None:
        response = daemon.post(
            "/internal/v1/probe",
            json={"storage_id": "HOT1", "path": "/../etc", "owner": OWNER},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PATH"

    def test_a_storage_this_daemon_does_not_serve_is_not_found(
        self, daemon: TestClient
    ) -> None:
        response = daemon.post(
            "/internal/v1/probe",
            json={"storage_id": "ELSEWHERE", "path": "/x", "owner": OWNER},
        )

        assert response.status_code == 404


class TestPrepare:
    def test_the_destination_is_created_and_addressable(
        self, daemon: TestClient, root: Path
    ) -> None:
        response = daemon.post(
            "/internal/v1/prepare",
            json={
                "storage_id": "HOT1",
                "name": "mydir",
                "owner": OWNER,
                "allocation_bytes": 1024,
            },
        )

        assert response.status_code == 200
        assert response.json()["path"] == str(root / "tester" / "mydir")
        assert (root / "tester" / "mydir").is_dir()

    def test_preparing_twice_is_the_same_answer(self, daemon: TestClient) -> None:
        body = {"storage_id": "HOT1", "name": "mydir", "owner": OWNER, "allocation_bytes": 1024}

        first = daemon.post("/internal/v1/prepare", json=body)
        second = daemon.post("/internal/v1/prepare", json=body)

        assert first.json() == second.json()


class TestTasks:
    def _start(self, daemon: TestClient, root: Path) -> dict[str, str]:
        (root / "myuser").mkdir(exist_ok=True)
        started: dict[str, str] = daemon.post(
            "/internal/v1/tasks",
            json={
                "transfer_id": 7,
                "storage_id": "HOT1",
                "source_path": "/myuser",
                "owner": OWNER,
                "target": {"path": str(root / "target")},
            },
        ).json()
        return started

    def test_a_task_is_accepted_and_can_be_asked_about(
        self, daemon: TestClient, root: Path, engine: FakeEngine
    ) -> None:
        started = self._start(daemon, root)

        state = daemon.get(f"/internal/v1/tasks/{started['task_id']}").json()

        assert state["transfer_id"] == 7
        assert state["state"] == "SUCCEEDED"
        assert state["bytes_done"] == 4096
        assert engine.calls[0].source.path == str((root / "myuser").resolve())

    def test_the_source_path_is_resolved_on_the_daemon(
        self, daemon: TestClient, root: Path
    ) -> None:
        response = daemon.post(
            "/internal/v1/tasks",
            json={
                "transfer_id": 7,
                "storage_id": "HOT1",
                "source_path": "/../etc",
                "owner": OWNER,
                "target": {"path": "/tmp/target"},
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PATH"

    def test_an_unknown_task_is_not_found(self, daemon: TestClient) -> None:
        assert daemon.get("/internal/v1/tasks/nope").status_code == 404

    def test_a_task_can_be_aborted(
        self, daemon: TestClient, root: Path, engine: FakeEngine
    ) -> None:
        started = self._start(daemon, root)

        response = daemon.delete(f"/internal/v1/tasks/{started['task_id']}")

        assert response.status_code == 200
        assert response.json()["state"] == "CANCELLED"
        assert engine.cancelled == [started["task_id"]]

    def test_aborting_an_unknown_task_is_not_found(self, daemon: TestClient) -> None:
        assert daemon.delete("/internal/v1/tasks/nope").status_code == 404

    def test_a_daemon_with_no_runner_says_so(
        self, storage_bootstrap: BootstrapConfig, root: Path
    ) -> None:
        driver = PosixDriver(
            "HOT1", fileset_prefix=root, identity=CurrentUserIdentity(), root=root
        )
        app = create_app(
            storage_bootstrap, peer_auth=TokenAuthProvider(TOKEN), drivers={"HOT1": driver}
        )
        client = TestClient(app)

        response = client.post(
            "/internal/v1/tasks",
            json={
                "transfer_id": 1,
                "storage_id": "HOT1",
                "source_path": "/x",
                "owner": OWNER,
                "target": {"path": "/tmp/t"},
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        assert response.status_code == 404
