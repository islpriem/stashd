"""Transfer kinds, states, cancellation and retry rules."""

from enum import StrEnum

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.failures import FailureClass


class TransferKind(StrEnum):
    WARM = "warm"
    FLUSH = "flush"
    RELEASE = "release"

    @property
    def moves_data(self) -> bool:
        return self is not TransferKind.RELEASE


class TransferState(StrEnum):
    SUBMITTED = "SUBMITTED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InvalidTransition(StashError):
    code = ErrorCode.CONFLICT


_TERMINAL = frozenset({TransferState.SUCCEEDED, TransferState.FAILED, TransferState.CANCELLED})

_TRANSITIONS: dict[TransferState, frozenset[TransferState]] = {
    TransferState.SUBMITTED: frozenset(
        {TransferState.ASSIGNED, TransferState.FAILED, TransferState.CANCELLED}
    ),
    TransferState.ASSIGNED: frozenset(
        {TransferState.RUNNING, TransferState.FAILED, TransferState.CANCELLED}
    ),
    TransferState.RUNNING: frozenset(
        {TransferState.SUCCEEDED, TransferState.FAILED, TransferState.CANCELLED}
    ),
    # Only a retry leaves a terminal state, and only from FAILED.
    TransferState.FAILED: frozenset({TransferState.SUBMITTED}),
    TransferState.SUCCEEDED: frozenset(),
    TransferState.CANCELLED: frozenset(),
}

# How far a transfer has come. Daemon events arrive duplicated and out of order, so an
# event that would move a transfer to an equal or lower rank is ignored.
_RANK = {
    TransferState.SUBMITTED: 0,
    TransferState.ASSIGNED: 1,
    TransferState.RUNNING: 2,
    TransferState.SUCCEEDED: 3,
    TransferState.FAILED: 3,
    TransferState.CANCELLED: 3,
}

# Retrying these can never help, whatever transfer.retries.retry_on says.
_NEVER_RETRIED = frozenset(
    {
        FailureClass.PERMISSION_DENIED,
        FailureClass.QUOTA_EXCEEDED,
        FailureClass.SOURCE_MISSING,
        FailureClass.CANCELLED,
    }
)


def next_transfer_state(current: TransferState, target: TransferState) -> TransferState:
    if target not in _TRANSITIONS[current]:
        raise InvalidTransition(
            f"a transfer in {current} cannot move to {target}",
            state=str(current),
            requested_state=str(target),
        )
    return target


def is_terminal(state: TransferState) -> bool:
    return state in _TERMINAL


def can_cancel(state: TransferState) -> bool:
    return not is_terminal(state)


def is_backwards(current: TransferState, target: TransferState) -> bool:
    return _RANK[target] <= _RANK[current]


def should_retry(
    failure: FailureClass, *, attempt: int, count: int, retry_on: frozenset[FailureClass]
) -> bool:
    return failure not in _NEVER_RETRIED and failure in retry_on and attempt <= count
