"""The rsync engine: what it runs, what it reads back, how it fails."""

import pytest

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.engines.base import TransferEndpoint, TransferOptions
from stashd.engines.rsync import RsyncEngine, classify_exit, parse_progress, parse_stats
from stashd.identity.base import Identity
from stashd.identity.current import CurrentUserIdentity
from stashd.identity.sudo import SudoIdentity

ALICE = Owner(user="alice", uid=1000, gid=1000)
SOURCE = TransferEndpoint(path="/gpfs/hot1/myuser/mydirectory")
TARGET = TransferEndpoint(path="/cache/loc2/alice/mydir")
MBIT = 1000**2 // 8


def command(options: TransferOptions, identity: Identity | None = None) -> list[str]:
    engine = RsyncEngine(identity or CurrentUserIdentity())
    return engine.command(SOURCE, TARGET, options, owner=ALICE)


class TestCommand:
    def test_the_command_is_the_one_the_spec_names(self) -> None:
        argv = command(TransferOptions())

        assert argv[0] == "rsync"
        assert "-a" in argv
        assert "--numeric-ids" in argv
        assert "--partial" in argv
        assert "--info=progress2,stats2" in argv

    def test_both_ends_carry_a_trailing_slash(self) -> None:
        argv = command(TransferOptions())

        assert argv[-2:] == ["/gpfs/hot1/myuser/mydirectory/", "/cache/loc2/alice/mydir/"]

    def test_a_bandwidth_limit_is_given_in_kibibytes_per_second(self) -> None:
        argv = command(TransferOptions(bwlimit_bytes_per_s=800 * MBIT))

        assert "--bwlimit=97656" in argv

    def test_no_limit_means_no_flag(self) -> None:
        assert not [flag for flag in command(TransferOptions()) if flag.startswith("--bwlimit")]

    def test_delete_is_only_used_when_asked_for(self) -> None:
        assert "--delete" not in command(TransferOptions())
        assert "--delete" in command(TransferOptions(delete=True))

    def test_the_command_runs_as_the_owner(self) -> None:
        argv = command(TransferOptions(), SudoIdentity())

        assert argv[:5] == ["sudo", "-n", "-u", "alice", "--"]
        assert argv[5] == "rsync"

    def test_a_remote_target_becomes_an_ssh_destination(self) -> None:
        engine = RsyncEngine(CurrentUserIdentity())
        remote = TransferEndpoint(
            path="/cache/loc2/alice/mydir", host="stash-loc2", user="alice"
        )

        argv = engine.command(SOURCE, remote, TransferOptions(), owner=ALICE)

        assert argv[-1] == "alice@stash-loc2:/cache/loc2/alice/mydir/"


class TestProgress:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("    32.77M   4%   31.25MB/s    0:00:01", 32_770_000),
            ("  1,234,567  10%  1.00MB/s    0:00:05", 1_234_567),
            ("    1.50G  99%   10.00MB/s    0:00:00", 1_500_000_000),
            ("        0   0%    0.00kB/s    0:00:00", 0),
        ],
    )
    def test_a_progress_line_yields_the_bytes_so_far(self, line: str, expected: int) -> None:
        assert parse_progress(line) == expected

    @pytest.mark.parametrize("line", ["", "sending incremental file list", "mydir/", "xfr#1"])
    def test_other_output_is_not_progress(self, line: str) -> None:
        assert parse_progress(line) is None


class TestStats:
    def test_the_transferred_size_and_file_count_are_read_back(self) -> None:
        output = """
Number of files: 12,043 (reg: 12,000, dir: 43)
Number of regular files transferred: 12,000
Total file size: 40,000,000,000 bytes
Total transferred file size: 37,580,963,840 bytes
Total bytes sent: 37,600,000,000
"""

        stats = parse_stats(output)

        assert stats.bytes_transferred == 37_580_963_840
        assert stats.files_transferred == 12_000

    def test_output_without_statistics_reports_nothing(self) -> None:
        stats = parse_stats("sending incremental file list\n")

        assert stats.bytes_transferred is None
        assert stats.files_transferred is None


class TestExitCodes:
    def test_success(self) -> None:
        assert classify_exit(0, "") is None

    @pytest.mark.parametrize("code", [10, 12, 5])
    def test_a_broken_connection_is_a_network_failure(self, code: int) -> None:
        assert classify_exit(code, "") is FailureClass.NETWORK

    @pytest.mark.parametrize("code", [30, 35])
    def test_a_stalled_transfer_is_a_timeout(self, code: int) -> None:
        assert classify_exit(code, "") is FailureClass.TIMEOUT

    def test_an_interrupted_transfer_is_cancelled(self) -> None:
        assert classify_exit(20, "") is FailureClass.CANCELLED

    def test_a_missing_source_is_recognised(self) -> None:
        stderr = 'rsync: change_dir "/gpfs/hot1/gone" failed: No such file or directory (2)'

        assert classify_exit(23, stderr) is FailureClass.SOURCE_MISSING

    def test_a_permission_problem_is_recognised(self) -> None:
        stderr = 'rsync: opendir "/gpfs/hot1/private" failed: Permission denied (13)'

        assert classify_exit(23, stderr) is FailureClass.PERMISSION_DENIED

    def test_a_full_target_is_recognised(self) -> None:
        stderr = (
            'rsync: write failed on "/cache/loc2/alice/mydir/big": No space left on device (28)'
        )

        assert classify_exit(11, stderr) is FailureClass.NO_SPACE

    def test_a_quota_overrun_is_recognised(self) -> None:
        stderr = "rsync: write failed: Disk quota exceeded (122)"

        assert classify_exit(11, stderr) is FailureClass.QUOTA_EXCEEDED

    @pytest.mark.parametrize("code", [1, 2, 4, 13, 14, 21, 22])
    def test_rsync_itself_going_wrong_is_a_tool_error(self, code: int) -> None:
        assert classify_exit(code, "") is FailureClass.TOOL_ERROR

    def test_a_partial_transfer_without_a_known_reason_is_a_tool_error(self) -> None:
        assert classify_exit(23, "something unexpected") is FailureClass.TOOL_ERROR

    def test_an_unknown_exit_code_is_internal(self) -> None:
        assert classify_exit(99, "") is FailureClass.INTERNAL
