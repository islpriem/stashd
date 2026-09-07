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
    # Classified here so a deployment can list them in
    # transfer.retries.retry_on.
    DAEMON_UNREACHABLE = "daemon_unreachable"
    DAEMON_SHUTDOWN = "daemon_shutdown"
    TOOL_ERROR = "tool_error"
    INTERNAL = "internal"
