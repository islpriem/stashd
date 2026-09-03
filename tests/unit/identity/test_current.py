"""The development identity really runs the command, as the daemon's own user."""

import pytest

from stashd.domain.storage import Owner
from stashd.identity.base import IdentityError
from stashd.identity.current import CurrentUserIdentity

ALICE = Owner(user="alice", uid=1000, gid=1000)


def test_a_command_runs_and_reports_its_output() -> None:
    result = CurrentUserIdentity().run(ALICE, ["echo", "hello"])

    assert result.ok
    assert result.stdout.strip() == "hello"


def test_a_failing_command_reports_its_status() -> None:
    result = CurrentUserIdentity().run(ALICE, ["false"])

    assert not result.ok


def test_a_missing_binary_is_an_identity_error() -> None:
    with pytest.raises(IdentityError):
        CurrentUserIdentity().run(ALICE, ["definitely-not-a-command-42"])


def test_a_command_that_overruns_its_timeout_is_stopped() -> None:
    with pytest.raises(IdentityError, match="timed out"):
        CurrentUserIdentity().run(ALICE, ["sleep", "5"], timeout=0.05)


def test_wrapping_changes_nothing() -> None:
    assert CurrentUserIdentity().wrap(ALICE, ["rsync", "-a"]) == ["rsync", "-a"]
