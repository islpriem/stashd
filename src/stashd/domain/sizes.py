"""IEC rendering, used only for the human ``message`` of an error envelope.

Every number a client may act on travels as integer bytes in ``details``.
"""

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    size = float(value)
    for unit in _UNITS[1:-1]:
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size / 1024:.1f} {_UNITS[-1]}"
