"""Logging setup using rich for nicer terminal output.

Usage:
    from src.utils.logging import get_logger
    log = get_logger(__name__)
    log.info("Processing %d jobs", n)
"""

from __future__ import annotations

import logging
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

_console = Console()
_handler: Optional[RichHandler] = None
_configured_level: Optional[str] = None


def configure_logging(level: str = "INFO") -> None:
    """Configure root logger with a RichHandler. Idempotent: re-applying
    only changes the level if it differs from the current one."""
    global _handler, _configured_level

    log_level = getattr(logging, level.upper(), logging.INFO)

    if _handler is None:
        _handler = RichHandler(
            console=_console,
            show_time=True,
            show_level=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=False,
        )
        _handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logging.root.addHandler(_handler)

    if _configured_level != level:
        logging.root.setLevel(log_level)
        _handler.setLevel(log_level)
        _configured_level = level


def get_logger(name: str) -> logging.Logger:
    """Return a logger; ensures root is configured at least at INFO."""
    if _handler is None:
        configure_logging("INFO")
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger"]