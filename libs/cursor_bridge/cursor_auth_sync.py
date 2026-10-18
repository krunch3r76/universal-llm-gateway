"""Sync Cursor auth keys from the operator profile into the Glass :8 profile."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_DEFAULT_SOURCE = Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"
_DEFAULT_TARGET = Path.home() / ".gateway/cursor-io/User/globalStorage/state.vscdb"

_AUTH_KEY_SQL = (
    "SELECT key, value FROM ItemTable WHERE key LIKE 'cursorAuth/%' "
    "OR key = 'glass.lastSignedInAuthId'"
)


def sync_cursor_io_auth(
    source_db: Path = _DEFAULT_SOURCE,
    target_db: Path = _DEFAULT_TARGET,
) -> dict[str, Any]:
    """Copy auth keys from ``source_db`` into ``target_db`` (INSERT OR REPLACE)."""
    if not source_db.is_file():
        return {"ok": False, "reason": "source_missing", "path": str(source_db)}
    target_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
        dst.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        rows = src.execute(_AUTH_KEY_SQL).fetchall()
        synced: list[str] = []
        for key, value in rows:
            dst.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (key, value),
            )
            synced.append(str(key))
        dst.commit()
    return {"ok": True, "synced_keys": synced}
