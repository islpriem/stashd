"""Becoming the requesting user."""

import subprocess
from collections.abc import Sequence

import pytest

from stashd.domain.storage import Owner
from stashd.identity.base import Completed, IdentityError
from stashd.identity.sudo import SudoIdentity

ALICE = Owner(user="alice", uid=1000, gid=1000)


def test_the_command_runs_through_non_interactive_sudo() -> None:
    seen: list[Sequence[str]] = []

    def runner(argv: Sequence[str], timeout: float) -> Completed:
        seen.append(argv)
        return Completed(0, "", "")

    SudoIdentity(runner).run(ALICE, ["mkdir", "-p", "/cache/alice/mydir"])

    assert list(seen[0]) == [
        "sudo",
        "-n",
        "-u",
        "alice",
        "--",
        "mkdir",
        "-p",
        "/cache/alice/mydir",
    ]


def test_the_exit_status_and_output_are_handed_back() -> None:
    def runner(argv: Sequence[str], timeout: float) -> Completed:
        return Completed(1, "out", "mkdir: permission denied")

    result = SudoIdentity(runner).run(ALICE, ["mkdir", "/root/x"])

    assert not result.ok
    assert result.stderr == "mkdir: permission denied"


def test_a_timeout_is_a_permission_error_not_a_hang() -> None:
    def runner(argv: Sequence[str], timeout: float) -> Completed:
        raise subprocess.TimeoutExpired(cmd="sudo", timeout=timeout)

    with pytest.raises(IdentityError) as excinfo:
        SudoIdentity(runner).run(ALICE, ["du", "-s", "/big"], timeout=5)

    assert "alice" in excinfo.value.message
    assert excinfo.value.details["user"] == "alice"


def test_a_missing_sudo_binary_is_reported() -> None:
    def runner(argv: Sequence[str], timeout: float) -> Completed:
        raise FileNotFoundError("sudo")

    with pytest.raises(IdentityError):
        SudoIdentity(runner).run(ALICE, ["mkdir", "/x"])


def test_the_timeout_is_passed_through() -> None:
    seen: list[float] = []

    def runner(argv: Sequence[str], timeout: float) -> Completed:
        seen.append(timeout)
        return Completed(0, "", "")

    SudoIdentity(runner).run(ALICE, ["true"], timeout=12.5)

    assert seen == [12.5]


def test_a_command_can_be_wrapped_for_a_caller_that_runs_it_itself() -> None:
    """The transfer engine owns its process so it can stream and kill it."""
    wrapped = SudoIdentity().wrap(ALICE, ["rsync", "-a", "/src/", "/dst/"])

    assert wrapped == ["sudo", "-n", "-u", "alice", "--", "rsync", "-a", "/src/", "/dst/"]
