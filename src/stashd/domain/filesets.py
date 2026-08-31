"""Fileset kinds, states and the transitions between them."""

from enum import StrEnum

from stashd.domain.errors import ErrorCode, StashError


class FilesetKind(StrEnum):
    CACHED = "cached"
    OUTPUT = "output"


class FilesetState(StrEnum):
    CREATING = "CREATING"
    READY = "READY"
    POPULATING = "POPULATING"
    FLUSHING = "FLUSHING"
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"
    FAILED = "FAILED"


class InvalidTransition(StashError):
    code = ErrorCode.CONFLICT


_TRANSITIONS: dict[FilesetState, frozenset[FilesetState]] = {
    FilesetState.CREATING: frozenset({FilesetState.READY, FilesetState.FAILED}),
    FilesetState.READY: frozenset(
        {
            FilesetState.POPULATING,
            FilesetState.FLUSHING,
            FilesetState.RELEASING,
            FilesetState.FAILED,
        }
    ),
    FilesetState.POPULATING: frozenset({FilesetState.READY, FilesetState.FAILED}),
    FilesetState.FLUSHING: frozenset({FilesetState.READY, FilesetState.FAILED}),
    FilesetState.RELEASING: frozenset({FilesetState.RELEASED, FilesetState.FAILED}),
    # A failed fileset keeps its allocation until its owner releases it, and may be retried.
    FilesetState.FAILED: frozenset(
        {FilesetState.POPULATING, FilesetState.FLUSHING, FilesetState.RELEASING}
    ),
    FilesetState.RELEASED: frozenset(),
}


def next_fileset_state(current: FilesetState, target: FilesetState) -> FilesetState:
    if target not in _TRANSITIONS[current]:
        raise InvalidTransition(
            f"a fileset in {current} cannot move to {target}",
            state=str(current),
            requested_state=str(target),
        )
    return target


def holds_allocation(state: FilesetState) -> bool:
    """Allocation is reserved from CREATING until RELEASED, whatever is used."""
    return state is not FilesetState.RELEASED
