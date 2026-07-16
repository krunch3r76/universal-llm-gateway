"""Lock/read model — pure functions over host row-assertions (F-M3)."""

from __future__ import annotations

import sqlite3
from typing import Any

from .strategy_row import pending, pin_ok, rows


def undisposed_count(conn: sqlite3.Connection, host: str) -> int:
    return sum(1 for r in rows(conn, host) if r.material and pending(r))


def lock_ready(
    conn: sqlite3.Connection,
    host: str,
    deliverable: str,
) -> tuple[bool, list[dict[str, Any]]]:
    """Return (ready, blocking_rows) for a deliverable-scoped lock check."""
    blocking: list[dict[str, Any]] = []
    for row in rows(conn, host):
        if not row.material or deliverable not in row.affects:
            continue
        if pending(row) or not pin_ok(conn, row):
            blocking.append(
                {
                    "host": host,
                    "row_id": row.row_id,
                    "assertion_id": row.assertion_id,
                    "pin": row.pin,
                    "affects": list(row.affects),
                    "reason": "pending" if pending(row) else "pin_unresolved",
                }
            )
    return (len(blocking) == 0, blocking)
