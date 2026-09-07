"""Fileset and transfer state machines."""

import pytest

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.failures import FailureClass
from stashd.domain.filesets import FilesetState, holds_allocation, next_fileset_state
from stashd.domain.transfers import (
    TransferKind,
    TransferState,
    can_cancel,
    is_backwards,
    next_transfer_state,
    should_retry,
)


class TestFilesetStates:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (FilesetState.CREATING, FilesetState.READY),
            (FilesetState.CREATING, FilesetState.FAILED),
            (FilesetState.READY, FilesetState.POPULATING),
            (FilesetState.READY, FilesetState.FLUSHING),
            (FilesetState.READY, FilesetState.RELEASING),
            (FilesetState.POPULATING, FilesetState.READY),
            (FilesetState.POPULATING, FilesetState.FAILED),
            (FilesetState.FLUSHING, FilesetState.READY),
            (FilesetState.RELEASING, FilesetState.RELEASED),
            (FilesetState.FAILED, FilesetState.RELEASING),
            (FilesetState.FAILED, FilesetState.POPULATING),
        ],
    )
    def test_allowed_transitions(self, current: FilesetState, target: FilesetState) -> None:
        assert next_fileset_state(current, target) is target

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (FilesetState.CREATING, FilesetState.POPULATING),
            (FilesetState.RELEASED, FilesetState.READY),
            (FilesetState.RELEASED, FilesetState.RELEASING),
            (FilesetState.READY, FilesetState.CREATING),
            (FilesetState.POPULATING, FilesetState.FLUSHING),
        ],
    )
    def test_refused_transitions(self, current: FilesetState, target: FilesetState) -> None:
        with pytest.raises(StashError) as excinfo:
            next_fileset_state(current, target)

        assert excinfo.value.code is ErrorCode.CONFLICT

    def test_every_state_but_released_holds_its_allocation(self) -> None:
        holding = {state for state in FilesetState if holds_allocation(state)}

        assert holding == set(FilesetState) - {FilesetState.RELEASED}

    def test_a_failed_fileset_still_holds_its_allocation(self) -> None:
        assert holds_allocation(FilesetState.FAILED)


class TestTransferStates:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (TransferState.SUBMITTED, TransferState.ASSIGNED),
            (TransferState.ASSIGNED, TransferState.RUNNING),
            (TransferState.RUNNING, TransferState.SUCCEEDED),
            (TransferState.RUNNING, TransferState.FAILED),
            (TransferState.RUNNING, TransferState.CANCELLED),
            (TransferState.FAILED, TransferState.SUBMITTED),
            (TransferState.ASSIGNED, TransferState.SUBMITTED),
        ],
    )
    def test_allowed_transitions(self, current: TransferState, target: TransferState) -> None:
        assert next_transfer_state(current, target) is target

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (TransferState.SUBMITTED, TransferState.RUNNING),
            (TransferState.SUCCEEDED, TransferState.RUNNING),
            (TransferState.CANCELLED, TransferState.SUBMITTED),
            (TransferState.RUNNING, TransferState.ASSIGNED),
        ],
    )
    def test_refused_transitions(self, current: TransferState, target: TransferState) -> None:
        with pytest.raises(StashError):
            next_transfer_state(current, target)

    @pytest.mark.parametrize(
        "state", [TransferState.SUBMITTED, TransferState.ASSIGNED, TransferState.RUNNING]
    )
    def test_cancel_is_allowed_while_a_transfer_is_alive(self, state: TransferState) -> None:
        assert can_cancel(state)

    @pytest.mark.parametrize(
        "state", [TransferState.SUCCEEDED, TransferState.FAILED, TransferState.CANCELLED]
    )
    def test_a_terminal_transfer_cannot_be_cancelled(self, state: TransferState) -> None:
        assert not can_cancel(state)

    def test_a_dispatch_that_never_landed_can_be_offered_again(self) -> None:
        """The controller may put it back; a late daemon event still may not."""
        assert next_transfer_state(TransferState.ASSIGNED, TransferState.SUBMITTED)
        assert is_backwards(TransferState.ASSIGNED, TransferState.SUBMITTED)

    def test_an_event_that_moves_backwards_is_recognised(self) -> None:
        assert is_backwards(TransferState.RUNNING, TransferState.ASSIGNED)
        assert is_backwards(TransferState.SUCCEEDED, TransferState.RUNNING)
        assert not is_backwards(TransferState.ASSIGNED, TransferState.RUNNING)

    def test_a_repeated_event_is_backwards_and_therefore_ignorable(self) -> None:
        assert is_backwards(TransferState.RUNNING, TransferState.RUNNING)


class TestRetries:
    def test_a_listed_failure_class_is_retried_until_the_count_is_used_up(self) -> None:
        retry_on = frozenset({FailureClass.NETWORK, FailureClass.TIMEOUT})

        assert should_retry(FailureClass.NETWORK, attempt=1, count=2, retry_on=retry_on)
        assert should_retry(FailureClass.TIMEOUT, attempt=2, count=2, retry_on=retry_on)
        assert not should_retry(FailureClass.NETWORK, attempt=3, count=2, retry_on=retry_on)

    @pytest.mark.parametrize(
        "failure",
        [FailureClass.PERMISSION_DENIED, FailureClass.QUOTA_EXCEEDED, FailureClass.CANCELLED],
    )
    def test_permission_quota_and_validation_failures_are_never_retried(
        self, failure: FailureClass
    ) -> None:
        retry_on = frozenset({FailureClass.NETWORK, failure})

        assert not should_retry(failure, attempt=1, count=2, retry_on=retry_on)


class TestKinds:
    def test_only_a_release_moves_no_data(self) -> None:
        assert not TransferKind.RELEASE.moves_data
        assert TransferKind.WARM.moves_data
        assert TransferKind.FLUSH.moves_data
