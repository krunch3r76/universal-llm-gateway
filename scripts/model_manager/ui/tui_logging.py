"""Manage-TUI logging isolation — keep WARNINGs off the Textual tty."""

from __future__ import annotations

import logging
import sys
from typing import Any


def _is_console_stream_handler(handler: logging.Handler) -> bool:
    """True for stderr/stdout StreamHandlers (not FileHandler subclasses)."""
    if isinstance(handler, logging.FileHandler):
        return False
    if not isinstance(handler, logging.StreamHandler):
        return False
    stream = getattr(handler, "stream", None)
    if stream is sys.stderr or stream is sys.stdout:
        return True
    return getattr(stream, "name", "") in ("<stderr>", "<stdout>")


def _iter_loggers() -> list[logging.Logger]:
    loggers = [logging.getLogger()]
    for obj in logging.Logger.manager.loggerDict.values():
        if isinstance(obj, logging.Logger):
            loggers.append(obj)
    return loggers


def silence_manage_tui_console_handlers() -> int:
    """Drop stderr/stdout handlers so in-process logs cannot punch the TUI.

    ``universal_logging`` defaults attach a JSON console handler to root (and
    to ``universal_logging`` with ``propagate=false``). Charter-runner ticks
    run inside the manage process; their WARNINGs write stderr and scroll over
    Textual. File handlers under ``LOG_DIR`` (``/tmp/logs/tui``) stay.

    Returns the number of handlers removed.
    """
    # Ensure handlers exist before stripping — first get_logger may dictConfig.
    try:
        from universal_logging import get_logger

        get_logger("scripts.model_manager.ui.tui_logging")
    except Exception:  # noqa: BLE001 — silence must not block TUI boot
        pass

    removed = 0
    for logger in _iter_loggers():
        for handler in list(logger.handlers):
            if not _is_console_stream_handler(handler):
                continue
            logger.removeHandler(handler)
            removed += 1
    return removed


def assert_no_console_handlers(logger: logging.Logger | None = None) -> list[Any]:
    """Return remaining console handlers (empty ⇒ isolated). Test helper."""
    targets = [logger] if logger is not None else _iter_loggers()
    return [
        h
        for lg in targets
        for h in lg.handlers
        if _is_console_stream_handler(h)
    ]


__all__ = [
    "assert_no_console_handlers",
    "silence_manage_tui_console_handlers",
]
