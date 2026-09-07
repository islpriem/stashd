"""The rsync engine.

Runs as the requesting user through the ``Identity``, streams progress, and kills
the whole process group on cancel so nothing survives the abort.
"""

import os
import re
import signal
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO

import structlog

from stashd.domain.failures import FailureClass
from stashd.domain.storage import Owner
from stashd.engines.base import (
    Progress,
    ProgressCallback,
    TransferEndpoint,
    TransferOptions,
    TransferResult,
)
from stashd.identity.base import Identity

logger = structlog.get_logger()

BASE_ARGUMENTS = ("-a", "--numeric-ids", "--partial", "--info=progress2,stats2")
KIB = 1024
# Never wait for a password or a host-key answer: a transfer must fail, not hang.
SSH_COMMAND = "ssh -o BatchMode=yes"

# "    32.77M   4%   31.25MB/s    0:00:01"
_PROGRESS = re.compile(r"^\s*([\d,.]+)([KMGT]?)\s+\d+%\s+\S+\s+\d+:\d{2}:\d{2}")
_SUFFIXES = {"": 1, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}
_TRANSFERRED_BYTES = re.compile(r"^Total transferred file size:\s*([\d,]+)", re.MULTILINE)
_TRANSFERRED_FILES = re.compile(
    r"^Number of regular files transferred:\s*([\d,]+)", re.MULTILINE
)

# rsync's own exit codes, as documented in its manual page.
_BY_CODE: dict[int, FailureClass] = {
    1: FailureClass.TOOL_ERROR,
    2: FailureClass.TOOL_ERROR,
    3: FailureClass.SOURCE_MISSING,
    4: FailureClass.TOOL_ERROR,
    5: FailureClass.NETWORK,
    6: FailureClass.NETWORK,
    10: FailureClass.NETWORK,
    12: FailureClass.NETWORK,
    13: FailureClass.TOOL_ERROR,
    14: FailureClass.TOOL_ERROR,
    20: FailureClass.CANCELLED,
    21: FailureClass.TOOL_ERROR,
    22: FailureClass.TOOL_ERROR,
    30: FailureClass.TIMEOUT,
    35: FailureClass.TIMEOUT,
}
# Codes that only say "something went wrong with a file"; the message says what.
_FROM_MESSAGE = (11, 23, 24)
_MESSAGES = (
    ("no space left", FailureClass.NO_SPACE),
    ("quota exceeded", FailureClass.QUOTA_EXCEEDED),
    ("permission denied", FailureClass.PERMISSION_DENIED),
    ("no such file or directory", FailureClass.SOURCE_MISSING),
)


@dataclass(frozen=True, slots=True)
class Stats:
    bytes_transferred: int | None
    files_transferred: int | None


def parse_progress(line: str) -> int | None:
    """Bytes transferred so far, from one ``--info=progress2`` update."""
    match = _PROGRESS.match(line)
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    return int(value * _SUFFIXES[match.group(2)])


def parse_stats(output: str) -> Stats:
    """What ``--info=stats2`` reported at the end."""

    def number(pattern: re.Pattern[str]) -> int | None:
        found = pattern.search(output)
        return int(found.group(1).replace(",", "")) if found else None

    return Stats(
        bytes_transferred=number(_TRANSFERRED_BYTES),
        files_transferred=number(_TRANSFERRED_FILES),
    )


def classify_exit(returncode: int, stderr: str) -> FailureClass | None:
    """One stable failure class per way rsync can end."""
    if returncode == 0:
        return None
    if returncode in _FROM_MESSAGE:
        lowered = stderr.lower()
        for text, failure in _MESSAGES:
            if text in lowered:
                return failure
        return FailureClass.TOOL_ERROR
    return _BY_CODE.get(returncode, FailureClass.INTERNAL)


class RsyncEngine:
    def __init__(self, identity: Identity, rsync: str = "rsync") -> None:
        self._identity = identity
        self._rsync = rsync
        self._running: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()

    def command(
        self,
        source: TransferEndpoint,
        target: TransferEndpoint,
        options: TransferOptions,
        *,
        owner: Owner,
    ) -> list[str]:
        argv = [self._rsync, *BASE_ARGUMENTS]
        if source.is_remote or target.is_remote:
            argv.extend(["-e", SSH_COMMAND])
        if options.bwlimit_bytes_per_s is not None:
            # rsync reads --bwlimit as KiB/s unless a suffix says otherwise.
            argv.append(f"--bwlimit={options.bwlimit_bytes_per_s // KIB}")
        if options.delete:
            argv.append("--delete")
        argv.extend([source.as_argument(), target.as_argument()])
        return self._identity.wrap(owner, argv)

    def run(
        self,
        source: TransferEndpoint,
        target: TransferEndpoint,
        options: TransferOptions,
        *,
        owner: Owner,
        handle: str,
        on_progress: ProgressCallback | None = None,
    ) -> TransferResult:
        argv = self.command(source, target, options, owner=owner)
        logger.info(
            "transfer.start",
            handle=handle,
            channel="ssh" if source.is_remote or target.is_remote else "local",
            target=target.as_argument(),
        )
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,  # its own process group, so cancel takes the children
        )
        with self._lock:
            self._running[handle] = process
        try:
            return self._collect(process, handle, on_progress, options.timeout_seconds)
        finally:
            with self._lock:
                self._running.pop(handle, None)

    def cancel(self, handle: str) -> bool:
        """Kill the process group and leave the partial data where it is."""
        with self._lock:
            process = self._running.get(handle)
            self._cancelled.add(handle)
        if process is None:
            return False
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:  # pragma: no cover - it finished as we asked
            return False
        return True

    def _collect(
        self,
        process: subprocess.Popen[str],
        handle: str,
        on_progress: ProgressCallback | None,
        timeout: float | None,
    ) -> TransferResult:
        seen = 0
        collected: list[str] = []
        if process.stdout is not None:
            for update in _updates(process.stdout):
                collected.append(update)
                moved = parse_progress(update)
                if moved is not None and moved != seen:
                    seen = moved
                    if on_progress is not None:
                        on_progress(Progress(bytes_done=moved))
        _, stderr = process.communicate(timeout=timeout)
        stats = parse_stats("\n".join(collected))
        with self._lock:
            cancelled = handle in self._cancelled
            self._cancelled.discard(handle)
        returncode = process.returncode
        failure = FailureClass.CANCELLED if cancelled else classify_exit(returncode, stderr)
        return TransferResult(
            failure=failure,
            returncode=returncode,
            bytes_transferred=stats.bytes_transferred if stats.bytes_transferred else seen,
            files_transferred=stats.files_transferred or 0,
            message=stderr.strip(),
        )


def _updates(stream: IO[str]) -> Iterator[str]:
    """rsync separates progress updates with a carriage return, not a newline."""
    buffer = ""
    while True:
        chunk = stream.read(512)
        if not chunk:
            break
        buffer += chunk
        parts = re.split(r"[\r\n]", buffer)
        buffer = parts.pop()
        yield from (part for part in parts if part)
    if buffer:
        yield buffer
