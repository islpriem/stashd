"""Stable failure classes reported by a storage daemon."""

from enum import StrEnum


class FailureClass(StrEnum):
    PERMISSION_DENIED = "permission_denied"
    SOURCE_MISSING = "source_missing"
    TARGET_MISSING = "target_missing"
    NO_SPACE = "no_space"
    QUOTA_EXCEEDED = "quota_exceeded"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    TOOL_ERROR = "tool_error"
    INTERNAL = "internal"
