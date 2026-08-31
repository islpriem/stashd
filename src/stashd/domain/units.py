"""Parsing of the size, duration and rate literals used in configuration.

One rule across the project: a bare decimal prefix is SI (``500G`` = 500 * 1000**3), an
``i`` prefix is IEC (``500Gi`` = 500 * 1024**3). Sizes are integer bytes and rates are
integer bytes per second; bit rates are divided by eight and must come out whole.
"""

import re
from datetime import timedelta

_SIZE_RE = re.compile(r"^(?P<value>\d+)\s*(?P<prefix>[kKMGTP])?(?P<iec>i)?B?$")
_RATE_BIT_RE = re.compile(r"^(?P<value>\d+)\s*(?P<prefix>[kKMGTP])?bit$")
_DURATION_RE = re.compile(r"(?P<value>\d+)(?P<unit>ns|us|ms|s|m|h|d)")
_DURATION_FULL_RE = re.compile(r"^(?:\d+(?:ns|us|ms|s|m|h|d))+$")

_SI = {None: 1, "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5}
_IEC = {None: 1, "k": 1024, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
_DURATION_UNITS = {
    "ns": timedelta(microseconds=0.001),
    "us": timedelta(microseconds=1),
    "ms": timedelta(milliseconds=1),
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
}


def parse_size(value: str | int) -> int:
    """Bytes from an integer or a literal such as ``100Gi``, ``500G``, ``1024B``."""
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"invalid size {value!r}: must not be negative")
        return value
    match = _SIZE_RE.match(value.strip())
    if match is None:
        raise ValueError(f"invalid size {value!r}: expected an integer with an optional suffix")
    factors = _IEC if match["iec"] else _SI
    return int(match["value"]) * factors[match["prefix"]]


def parse_duration(value: str) -> timedelta:
    """Go-style duration such as ``5s``, ``7d`` or ``1h30m``. ``d`` is 24 hours."""
    text = value.strip()
    if not _DURATION_FULL_RE.match(text):
        raise ValueError(f"invalid duration {value!r}: expected e.g. 30s, 5m, 7d, 1h30m")
    return sum(
        (int(m["value"]) * _DURATION_UNITS[m["unit"]] for m in _DURATION_RE.finditer(text)),
        timedelta(),
    )


def parse_rate(value: str | int) -> int:
    """Bytes per second from ``2Gbit``, ``200MB/s``, ``10MiB/s`` or a plain integer."""
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"invalid rate {value!r}: must not be negative")
        return value
    text = value.strip()
    bits = _RATE_BIT_RE.match(text)
    if bits is not None:
        total = int(bits["value"]) * _SI[bits["prefix"]]
        if total % 8:
            raise ValueError(f"invalid rate {value!r}: not a whole number of bytes per second")
        return total // 8
    try:
        return parse_size(text.removesuffix("/s") if text.endswith("/s") else text)
    except ValueError:
        raise ValueError(
            f"invalid rate {value!r}: expected e.g. 2Gbit, 200MB/s, 10MiB/s"
        ) from None
