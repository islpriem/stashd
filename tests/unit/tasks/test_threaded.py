"""Transfers run beside the request, and what happens is reported."""

import os
from pathlib import Path

import pytest

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.drivers.base import StorageDriver
from stashd.drivers.posix import PosixDriver
from stashd.engines.base import TransferEndpoint
from stashd.engines.fake import FakeEngine
from stashd.identity.current import CurrentUserIdentity
from stashd.tasks.runner import TaskProgress, TransferTask
from stashd.tasks.threaded import ThreadTaskRunner

OWNER = Owner(user="tester", uid=os.getuid(), gid=os.getgid())


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[tuple[int, str, dict[str, object]]] = []

    def report(self, transfer_id: int, kind: str, **fields: object) -> None:
        self.events.append((transfer_id, kind, fields))


@pytest.fixture
def drivers(tmp_path: Path) -> dict[str, StorageDriver]:
    (tmp_path / "myuser").mkdir()
    return {
        "HOT1": PosixDriver(
            "HOT1", fileset_prefix=tmp_path, identity=CurrentUserIdentity(), root=tmp_path
        )
    }


def task() -> TransferTask:
    return TransferTask(
        transfer_id=7,
        storage_id="HOT1",
        source_path="/myuser",
        owner=OWNER,
        target=TransferEndpoint(path="/somewhere"),
    )


def finished(runner: ThreadTaskRunner, task_id: str) -> TaskProgress:
    runner._futures[task_id].result(timeout=10)
    done = runner.progress(task_id)
    assert done is not None
    return done


def test_a_transfer_reports_started_and_finished(
    drivers: dict[str, StorageDriver],
) -> None:
    reporter = RecordingReporter()
    engine = FakeEngine(bytes_transferred=4096, files_transferred=2, progress_steps=(2048,))
    runner = ThreadTaskRunner(drivers, engine, reporter)

    state = finished(runner, runner.submit(task()))

    assert state.state == "SUCCEEDED"
    assert [kind for _, kind, _ in reporter.events] == ["started", "progress", "finished"]
    assert reporter.events[-1][2]["bytes_done"] == 4096


def test_a_failure_is_reported_with_its_class(drivers: dict[str, StorageDriver]) -> None:
    reporter = RecordingReporter()
    engine = FakeEngine(failure=FailureClass.PERMISSION_DENIED)
    runner = ThreadTaskRunner(drivers, engine, reporter)

    state = finished(runner, runner.submit(task()))

    assert state.state == "FAILED"
    assert reporter.events[-1][1] == "failed"
    assert reporter.events[-1][2]["failure"] == "permission_denied"


def test_a_crash_still_reaches_the_controller(drivers: dict[str, StorageDriver]) -> None:
    reporter = RecordingReporter()
    runner = ThreadTaskRunner(drivers, FakeEngine(), reporter)
    broken = TransferTask(
        transfer_id=8,
        storage_id="NOWHERE",
        source_path="/myuser",
        owner=OWNER,
        target=TransferEndpoint(path="/x"),
    )

    state = finished(runner, runner.submit(broken))

    assert state.state == "FAILED"
    assert reporter.events[-1][1] == "failed"
    assert reporter.events[-1][2]["failure"] == "internal"


def test_aborting_a_task_reaches_the_engine(drivers: dict[str, StorageDriver]) -> None:
    engine = FakeEngine()
    runner = ThreadTaskRunner(drivers, engine, RecordingReporter())
    task_id = runner.submit(task())
    finished(runner, task_id)

    assert runner.abort(task_id) is True
    assert engine.cancelled == [task_id]


def test_aborting_something_unknown_says_so(drivers: dict[str, StorageDriver]) -> None:
    runner = ThreadTaskRunner(drivers, FakeEngine(), RecordingReporter())

    assert runner.abort("nope") is False
    assert runner.progress("nope") is None
