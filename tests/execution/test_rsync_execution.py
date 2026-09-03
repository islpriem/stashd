"""Real processes: cancellation everywhere, real rsync where there is one."""

import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.engines.base import TransferEndpoint, TransferOptions
from stashd.engines.rsync import RsyncEngine
from stashd.identity.current import CurrentUserIdentity

OWNER = Owner(user="tester", uid=0, gid=0)


def gnu_rsync() -> bool:
    """openrsync, which macOS ships, has neither --info=progress2 nor stats2."""
    found = shutil.which("rsync")
    if found is None:
        return False
    version = subprocess.run([found, "--version"], capture_output=True, text=True, check=False)
    return "openrsync" not in version.stdout.lower()


needs_rsync = pytest.mark.skipif(
    not gnu_rsync(), reason="needs GNU rsync (run in the e2e container)"
)


class TestCancellation:
    def test_cancelling_kills_the_process_and_reports_it(self, tmp_path: Path) -> None:
        """Nothing about this depends on rsync: it is about owning the process group."""
        script = tmp_path / "slow"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o755)
        engine = RsyncEngine(CurrentUserIdentity(), rsync=str(script))
        results = []

        def transfer() -> None:
            results.append(
                engine.run(
                    TransferEndpoint(path=str(tmp_path)),
                    TransferEndpoint(path=str(tmp_path)),
                    TransferOptions(),
                    owner=OWNER,
                    handle="t-1",
                )
            )

        worker = threading.Thread(target=transfer)
        worker.start()
        # Waiting for a real process to exist, not for a schedule: poll until it is there.
        for _ in range(500):
            if engine.cancel("t-1"):
                break
            time.sleep(0.01)
        worker.join(timeout=10)

        assert results and results[0].failure is FailureClass.CANCELLED

    def test_cancelling_something_that_is_not_running_says_so(self) -> None:
        assert RsyncEngine(CurrentUserIdentity()).cancel("nobody") is False


@needs_rsync
class TestRealRsync:
    @pytest.fixture
    def source(self, tmp_path: Path) -> Path:
        directory = tmp_path / "source"
        (directory / "nested").mkdir(parents=True)
        (directory / "one").write_bytes(b"a" * 4096)
        (directory / "nested" / "two").write_bytes(b"b" * 8192)
        return directory

    def engine(self) -> RsyncEngine:
        return RsyncEngine(CurrentUserIdentity())

    def test_a_directory_is_copied_and_the_statistics_come_back(
        self, source: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()

        result = self.engine().run(
            TransferEndpoint(path=str(source)),
            TransferEndpoint(path=str(target)),
            TransferOptions(),
            owner=OWNER,
            handle="t-1",
        )

        assert result.ok, result.message
        assert (target / "one").read_bytes() == b"a" * 4096
        assert (target / "nested" / "two").exists()
        assert result.files_transferred == 2
        assert result.bytes_transferred == 4096 + 8192

    def test_progress_is_reported_while_it_runs(self, source: Path, tmp_path: Path) -> None:
        (source / "big").write_bytes(b"c" * (8 * 1024 * 1024))
        target = tmp_path / "target"
        target.mkdir()
        seen: list[int] = []

        result = self.engine().run(
            TransferEndpoint(path=str(source)),
            TransferEndpoint(path=str(target)),
            TransferOptions(),
            owner=OWNER,
            handle="t-1",
            on_progress=lambda progress: seen.append(progress.bytes_done),
        )

        assert result.ok
        assert seen, "rsync reported no progress at all"
        assert max(seen) > 0

    def test_a_refresh_deletes_what_the_source_no_longer_has(
        self, source: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "target"
        target.mkdir()
        (target / "stale").write_text("from an older warm")

        result = self.engine().run(
            TransferEndpoint(path=str(source)),
            TransferEndpoint(path=str(target)),
            TransferOptions(delete=True),
            owner=OWNER,
            handle="t-1",
        )

        assert result.ok
        assert not (target / "stale").exists()

    def test_without_delete_the_target_keeps_what_it_had(
        self, source: Path, tmp_path: Path
    ) -> None:
        """A flush merges into the target; it never removes the user's data."""
        target = tmp_path / "target"
        target.mkdir()
        (target / "kept").write_text("results from before")

        result = self.engine().run(
            TransferEndpoint(path=str(source)),
            TransferEndpoint(path=str(target)),
            TransferOptions(),
            owner=OWNER,
            handle="t-1",
        )

        assert result.ok
        assert (target / "kept").read_text() == "results from before"

    def test_a_source_that_is_not_there_is_classified(self, tmp_path: Path) -> None:
        result = self.engine().run(
            TransferEndpoint(path=str(tmp_path / "absent")),
            TransferEndpoint(path=str(tmp_path)),
            TransferOptions(),
            owner=OWNER,
            handle="t-1",
        )

        assert result.failure is FailureClass.SOURCE_MISSING

    def test_a_bandwidth_limit_is_accepted_by_rsync(self, source: Path, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()

        result = self.engine().run(
            TransferEndpoint(path=str(source)),
            TransferEndpoint(path=str(target)),
            TransferOptions(bwlimit_bytes_per_s=50 * 1024 * 1024),
            owner=OWNER,
            handle="t-1",
        )

        assert result.ok, result.message
