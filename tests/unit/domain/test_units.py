"""Unit parsing for configuration values."""

from datetime import timedelta

import pytest

from stashd.domain.units import parse_duration, parse_rate, parse_size


class TestParseSize:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0", 0),
            ("1024", 1024),
            ("1024B", 1024),
            ("100Gi", 100 * 1024**3),
            ("100GiB", 100 * 1024**3),
            ("500Ti", 500 * 1024**4),
            ("1Ki", 1024),
            ("2Mi", 2 * 1024**2),
            ("4Pi", 4 * 1024**5),
        ],
    )
    def test_iec_suffixes_parse(self, text: str, expected: int) -> None:
        assert parse_size(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("500G", 500 * 1000**3), ("500GB", 500 * 1000**3), ("2T", 2 * 1000**4), ("1k", 1000)],
    )
    def test_si_suffixes_are_powers_of_1000(self, text: str, expected: int) -> None:
        assert parse_size(text) == expected

    def test_si_and_iec_differ(self) -> None:
        assert parse_size("500G") != parse_size("500Gi")

    def test_whitespace_is_tolerated(self) -> None:
        assert parse_size(" 100 Gi ") == 100 * 1024**3

    def test_plain_int_passes_through(self) -> None:
        assert parse_size(1024) == 1024

    @pytest.mark.parametrize("text", ["", "Gi", "100Xi", "1.5Gi", "-1", "100 Gi B", "one"])
    def test_malformed_input_is_rejected(self, text: str) -> None:
        with pytest.raises(ValueError, match="size"):
            parse_size(text)

    def test_negative_int_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="size"):
            parse_size(-1)


class TestParseDuration:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5s", timedelta(seconds=5)),
            ("60s", timedelta(minutes=1)),
            ("7d", timedelta(days=7)),
            ("365d", timedelta(days=365)),
            ("500ms", timedelta(milliseconds=500)),
            ("1h30m", timedelta(hours=1, minutes=30)),
            ("2h45m10s", timedelta(hours=2, minutes=45, seconds=10)),
            ("0s", timedelta(0)),
        ],
    )
    def test_go_style_durations_parse(self, text: str, expected: timedelta) -> None:
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "5", "5x", "-5s", "s5", "1h30", "1.5h"])
    def test_malformed_input_is_rejected(self, text: str) -> None:
        with pytest.raises(ValueError, match="duration"):
            parse_duration(text)


class TestParseRate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2Gbit", 250_000_000),
            ("800Mbit", 100_000_000),
            ("50Mbit", 6_250_000),
            ("200MB/s", 200_000_000),
            ("120MB/s", 120_000_000),
            ("10MiB/s", 10 * 1024**2),
            ("1000", 1000),
        ],
    )
    def test_bit_and_byte_rates_become_bytes_per_second(self, text: str, expected: int) -> None:
        assert parse_rate(text) == expected

    def test_plain_int_passes_through(self) -> None:
        assert parse_rate(1000) == 1000

    def test_negative_int_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            parse_rate(-1)

    def test_bit_rate_that_is_not_a_whole_number_of_bytes_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="rate"):
            parse_rate("1bit")

    @pytest.mark.parametrize("text", ["", "2Gbits", "MB/s", "200MB/h", "-5Mbit"])
    def test_malformed_input_is_rejected(self, text: str) -> None:
        with pytest.raises(ValueError, match="rate"):
            parse_rate(text)
