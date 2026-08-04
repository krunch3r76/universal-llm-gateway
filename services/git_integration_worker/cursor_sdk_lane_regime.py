"""Fleet-wide Lane-B regime switch (DDL + read/write; default ON per row-10)."""

from __future__ import annotations

import sqlite3

from services.git_integration_worker.cursor_dispatch_ledger import _connect

_REGIME_KEY = "lane_b_regime"
_REGIME_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_regime (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def ensure_regime_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_REGIME_DDL)


def lane_b_regime_active() -> bool:
    """True when fleet-wide default admit selects Lane-B (explicit opt-out still wins)."""
    with _connect() as conn:
        ensure_regime_schema(conn)
        row = conn.execute(
            "SELECT value FROM cursor_sdk_regime WHERE key=?",
            (_REGIME_KEY,),
        ).fetchone()
    if row is None:
        return True
    return str(row["value"]).lower() == "on"


def set_lane_b_regime(*, active: bool) -> None:
    """Persist regime switch; revert path is ``set_lane_b_regime(active=False)``."""
    with _connect() as conn:
        ensure_regime_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_regime (key, value) VALUES (?, ?)",
            (_REGIME_KEY, "on" if active else "off"),
        )


__all__ = [
    "ensure_regime_schema",
    "lane_b_regime_active",
    "set_lane_b_regime",
]
