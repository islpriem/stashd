"""Reference syntax and fileset naming.

``STORAGE:/path`` addresses a path inside a storage, ``STORAGE:name`` a fileset on it; the
leading slash is the only disambiguator, so a fileset name may never contain one. Paths are
storage-relative — a real mount point never appears in user input, and a path that could
leave the storage is refused here as well as re-checked on the daemon that owns it.
"""

import re
from dataclasses import dataclass

from stashd.domain.errors import InvalidName, InvalidPath

STORAGE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
FILESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class PathRef:
    """A path inside a storage system, storage-relative and starting with ``/``."""

    storage: str
    path: str

    def __str__(self) -> str:
        return f"{self.storage}:{self.path}"


@dataclass(frozen=True, slots=True)
class FilesetRef:
    """A named fileset on a storage system."""

    storage: str
    name: str

    def __str__(self) -> str:
        return f"{self.storage}:{self.name}"


def validate_storage_id(storage_id: str) -> str:
    if not STORAGE_ID_RE.match(storage_id):
        raise InvalidName(
            f"{storage_id!r} is not a storage id (uppercase letters, digits, - and _)",
            storage=storage_id,
        )
    return storage_id


def validate_fileset_name(name: str) -> str:
    if not FILESET_NAME_RE.match(name):
        raise InvalidName(
            f"{name!r} is not a fileset name: {FILESET_NAME_RE.pattern}",
            name=name,
        )
    return name


def validate_storage_path(storage: str, path: str) -> str:
    if not path.startswith("/"):
        raise InvalidPath(f"{path!r} is not storage-relative: it must start with /", path=path)
    path = path.rstrip("/") or "/"
    segments = path.split("/")[1:] if path != "/" else []
    if any(segment in ("", ".", "..") for segment in segments):
        raise InvalidPath(
            f"{storage}:{path} is not a plain path: empty, . and .. segments are refused",
            path=path,
        )
    return path


def parse_reference(text: str) -> PathRef | FilesetRef:
    """Split ``STORAGE:/path`` or ``STORAGE:name`` into its parts."""
    storage, separator, rest = text.partition(":")
    if not separator or not rest:
        raise InvalidName(
            f"{text!r} is not a reference: expected STORAGE:/path or STORAGE:name"
        )
    validate_storage_id(storage)
    if rest.startswith("/"):
        return PathRef(storage, validate_storage_path(storage, rest))
    return FilesetRef(storage, validate_fileset_name(rest))


def fileset_path(fileset_prefix: str, owner_user: str, name: str) -> str:
    """The deterministic location of a fileset, so a job script can compute it."""
    return f"{fileset_prefix.rstrip('/')}/{owner_user}/{validate_fileset_name(name)}"
