"""Transaction helpers for W4 fold writes (lock, commit, post-commit emit)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager


@contextmanager
def fold_write_lock(conn: sqlite3.Connection) -> Generator[None, None, None]:
    """Save isolation level, ``BEGIN IMMEDIATE``, restore level in ``finally``."""
    saved_iso = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        yield
    finally:
        conn.isolation_level = saved_iso


def commit_and_emit(
    conn: sqlite3.Connection, post_emits: list[Callable[[], None]]
) -> tuple[int, str | None]:
    """Commit the transaction and run post-commit callbacks; return count and last error."""
    conn.commit()
    emit_err: str | None = None
    emitted = 0
    for fn in post_emits:
        try:
            fn()
            emitted += 1
        except Exception as exc:  # noqa: BLE001
            emit_err = str(exc)
    return emitted, emit_err
