"""Structured errors for event-store query paths."""

from __future__ import annotations

import sqlite3


class EventStoreBusyError(sqlite3.OperationalError):
    """SQLite returned SQLITE_BUSY after busy_timeout (lock wait exhausted)."""


class EventStoreReadDeadlineError(Exception):
    """Raised when a read exceeds the server-side deadline and is abandoned.

    The store interrupts the reader connection so the bounded read pool can
    take an index-fast query instead of staying pinned on the abandoned scan.
    Callers distinguish this from SQLITE_BUSY (``EventStoreBusyError``) and
    from ordinary SQL failures, which stay on the sqlite3 error path.
    """


def is_sqlite_busy(exc: BaseException) -> bool:
    """True when ``exc`` indicates the database was locked under contention."""
    if not isinstance(exc, sqlite3.Error):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code == sqlite3.SQLITE_BUSY:
        return True
    message = str(exc).lower()
    return "database is locked" in message or "sqlite_busy" in message
