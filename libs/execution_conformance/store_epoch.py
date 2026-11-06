"""Persisted store epoch — singleton UUID per SQLite owner DB file."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_EPOCH_DDL = """
CREATE TABLE IF NOT EXISTS _execution_store_epoch (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def intent_tag_body(identity_key: str) -> str:
    """Raw intent line stored in bus turn bodies (case-sensitive, unescaped)."""
    return f"intent:{identity_key}\n"


def intent_tag_length(identity_key: str) -> int:
    return len(intent_tag_body(identity_key))


def persisted_store_epoch(db_path: Path) -> str:
    """Return stable epoch_id for *db_path*, or ``""`` when unreadable (fail closed)."""
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return ""
    try:
        with closing(sqlite3.connect(path, timeout=5.0)) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(_EPOCH_DDL)
            ts = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _execution_store_epoch "
                "(singleton, epoch_id, created_at) VALUES (1, ?, ?)",
                (uuid4().hex, ts),
            )
            conn.commit()
            row = conn.execute(
                "SELECT epoch_id FROM _execution_store_epoch WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return ""
            return str(row[0])
    except sqlite3.Error:
        return ""
