"""Domain exceptions and their stable wire codes.

Every rejection raises one of these; a single FastAPI handler turns it into the error
envelope. ``details`` carries the numbers the client renders — always integer bytes.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_NAME = "INVALID_NAME"
    INVALID_PATH = "INVALID_PATH"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_A_SOURCE_STORAGE = "NOT_A_SOURCE_STORAGE"
    NOT_A_CACHE_STORAGE = "NOT_A_CACHE_STORAGE"
    FILESET_EXISTS = "FILESET_EXISTS"
    FILESET_NOT_READY = "FILESET_NOT_READY"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    ALLOCATION_LIMIT_EXCEEDED = "ALLOCATION_LIMIT_EXCEEDED"
    TOTAL_ALLOCATION_LIMIT_EXCEEDED = "TOTAL_ALLOCATION_LIMIT_EXCEEDED"
    STORAGE_FULL = "STORAGE_FULL"
    OVER_ALLOCATION = "OVER_ALLOCATION"
    TOO_MANY_FILESETS = "TOO_MANY_FILESETS"
    TOO_MANY_QUEUED = "TOO_MANY_QUEUED"
    FLUSH_TARGET_REQUIRED = "FLUSH_TARGET_REQUIRED"
    CONFLICT = "CONFLICT"
    STORAGE_DRAINED = "STORAGE_DRAINED"
    DAEMON_UNAVAILABLE = "DAEMON_UNAVAILABLE"
    CONFIG_STALE = "CONFIG_STALE"
    INTERNAL = "INTERNAL"


class StashError(Exception):
    """Base of every rejection that reaches a client."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, **details: Any) -> None:
        self.message = message
        self.details: dict[str, Any] = details
        super().__init__(message)


class NotFound(StashError):
    code = ErrorCode.NOT_FOUND


class InvalidName(StashError):
    code = ErrorCode.INVALID_NAME


class InvalidPath(StashError):
    code = ErrorCode.INVALID_PATH
