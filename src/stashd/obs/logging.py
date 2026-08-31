"""structlog configuration: JSON in production, console for a terminal."""

import logging
from typing import TextIO

import structlog

from stashd.config.bootstrap import LogFormat, LogLevel


def configure_logging(
    level: LogLevel, log_format: LogFormat, *, stream: TextIO | None = None
) -> None:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    factory = (
        structlog.WriteLoggerFactory(file=stream) if stream else structlog.PrintLoggerFactory()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level]
        ),
        logger_factory=factory,
        cache_logger_on_first_use=False,
    )
