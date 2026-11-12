"""Structured logging via structlog.

Emits JSON when ``APP_ENV=production``, otherwise a readable console
format. Tokens, API keys, and passwords are never logged here; this
module does not render configuration values.
"""

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Configure structlog and the stdlib logging root once at startup."""
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(message)s",
    )

    common = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.app_env == "production":
        processors = [
            *common,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [*common, structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
