"""
sentinel-gujarat/backend/app/core/logging_config.py
-----------------------------------------------------
Structured logging setup for the entire project.

Import `get_logger` and call it at the top of each module:

    from backend.app.core.logging_config import get_logger
    logger = get_logger(__name__)

The log level is controlled by LOG_LEVEL in .env.
Sensitive values (passwords, API keys) must NEVER appear in log messages.
"""

import logging
import sys
from backend.app.core.config import settings


def _build_formatter() -> logging.Formatter:
    """
    Returns a formatter that produces readable, structured log lines.
    Example:
        2026-08-29 15:30:01 [INFO ] camera.catalogue | Camera catalogue loaded: 12 cameras
    """
    fmt = "%(asctime)s [%(levelname)-5s] %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    return logging.Formatter(fmt=fmt, datefmt=datefmt)


def configure_logging() -> None:
    """
    Call once at application startup (e.g. in main.py or a script entry point).
    Sets up a console handler with the level from settings.
    """
    root = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    # Avoid adding duplicate handlers if called more than once
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_build_formatter())
        root.addHandler(handler)

    # Quiet down noisy third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Returns a module-level logger.  Always prefer this over logging.getLogger()
    so every logger is guaranteed to follow the project's format.
    """
    configure_logging()  # idempotent — safe to call multiple times
    return logging.getLogger(name)
