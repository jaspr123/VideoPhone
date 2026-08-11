"""Logging configuration for the booth application.

Architecture rule 10: errors must be logged with timestamps and session IDs.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(session_id)s] %(message)s"
_DEFAULT_SESSION_ID = "-"


class _DefaultSessionFilter(logging.Filter):
    """Ensures every log record has a session_id, even outside a session."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = _DEFAULT_SESSION_ID
        return True


def setup_logging(
    log_dir: str | Path,
    level: int = logging.INFO,
    log_filename: str = "booth.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("video_guestbook")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addFilter(_DefaultSessionFilter())

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_dir / log_filename, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.propagate = False
    return logger


def get_session_adapter(logger: logging.Logger, session_id: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logger, {"session_id": session_id})
