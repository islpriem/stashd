"""IEC rendering for the human half of an error message."""

import pytest

from stashd.domain.sizes import format_bytes


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KiB"),
        (22548578304, "21.0 GiB"),
        (12884901888, "12.0 GiB"),
        (107374182400, "100.0 GiB"),
        (500 * 1024**4, "500.0 TiB"),
        (2 * 1024**6, "2.0 EiB"),
    ],
)
def test_bytes_render_in_iec_with_one_decimal(value: int, expected: str) -> None:
    assert format_bytes(value) == expected
