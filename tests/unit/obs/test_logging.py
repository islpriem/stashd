"""Structured logging setup."""

import io
import json

import pytest
import structlog

from stashd.config.bootstrap import LogFormat, LogLevel
from stashd.obs.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()


def test_json_logs_carry_level_timestamp_and_bound_context() -> None:
    stream = io.StringIO()
    configure_logging(LogLevel.INFO, LogFormat.JSON, stream=stream)

    structlog.contextvars.bind_contextvars(request_id="01JABC", user="mmustermann")
    try:
        structlog.get_logger().info("transfer.submitted", transfer_id=123456)
    finally:
        structlog.contextvars.clear_contextvars()

    record = json.loads(stream.getvalue())
    assert record["event"] == "transfer.submitted"
    assert record["level"] == "info"
    assert record["request_id"] == "01JABC"
    assert record["user"] == "mmustermann"
    assert record["transfer_id"] == 123456
    assert record["timestamp"].endswith("Z")


def test_level_filters_quieter_records() -> None:
    stream = io.StringIO()
    configure_logging(LogLevel.WARNING, LogFormat.JSON, stream=stream)

    structlog.get_logger().info("ignored")
    structlog.get_logger().warning("kept")

    assert [json.loads(line)["event"] for line in stream.getvalue().splitlines()] == ["kept"]


def test_console_format_is_plain_text() -> None:
    stream = io.StringIO()
    configure_logging(LogLevel.DEBUG, LogFormat.CONSOLE, stream=stream)

    structlog.get_logger().debug("daemon.starting", daemon_id="hot1")

    output = stream.getvalue()
    assert "daemon.starting" in output
    assert "daemon_id" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)
