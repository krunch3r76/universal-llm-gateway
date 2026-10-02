"""Structured errors for event-store query paths."""

from __future__ import annotations

import sqlite3


class EventStoreBusyError(sqlite3.OperationalError):
    """SQLite returned SQLITE_BUSY after busy_timeout (lock wait exhausted)."""


def is_sqlite_busy(exc: BaseException) -> bool:
    """True when ``exc`` indicates the database was locked under contention."""
    if not isinstance(exc, sqlite3.Error):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code == sqlite3.SQLITE_BUSY:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "sqlite_busy" in message
