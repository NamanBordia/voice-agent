"""Structured logging configuration using loguru.

Sets up dual-sink logging for the voice agent:
- Console (stderr): human-readable, colored, for debugging
- File (JSON): machine-readable, serialized, for later analysis

Called once at application startup from main.py.
"""

from __future__ import annotations

import sys

from loguru import logger


def setup_logging(*, log_level: str = "INFO") -> None:
    """Configure structured logging for the voice agent application.

    Args:
        log_level: Minimum log level for both sinks. Defaults to INFO.
    """

    # Remove default loguru handler
    logger.remove()

    # Console sink — human-readable, colored, includes bound context
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "{extra} - <level>{message}</level>"
        ),
        enqueue=True,
        diagnose=False,
    )

    # File sink — JSON-serialized for log aggregation and analysis
    logger.add(
        "logs/voice_agent.log",
        level=log_level,
        serialize=True,
        rotation="50 MB",
        retention="7 days",
        enqueue=True,
        diagnose=False,
    )
