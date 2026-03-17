from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("cortex-api.db")

_CORTEX_DB = Path(os.environ.get("CORTEX_DB_PATH", "/data/cortex/cortex.db"))
_TODOS_DB = Path(os.environ.get("TODOS_DB_PATH", "/data/cortex/todos.db"))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def cortex_conn() -> sqlite3.Connection:
    return _connect(_CORTEX_DB)


def todos_conn() -> sqlite3.Connection:
    return _connect(_TODOS_DB)


def query(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def execute(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount


def check_cortex_db() -> bool:
    return _CORTEX_DB.exists()


def check_todos_db() -> bool:
    return _TODOS_DB.exists()


def json_encode(value: Any) -> str | None:
    """Serialize a structured value to JSON TEXT for SQLite storage.

    None passthrough keeps nullable columns NULL rather than the string 'null'.
    """
    if value is None:
        return None
    return json.dumps(value)


def json_decode(value: str | None, *, fallback: Any = None) -> Any:
    """Deserialize a JSON TEXT column from SQLite back to a Python object.

    Returns *fallback* for NULL columns or unparseable values so that
    pre-backfill rows don't crash reads.
    """
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "json_decode: unparseable value %r, returning fallback", value[:80]
        )
        return fallback


def decode_row(
    row: dict[str, Any], json_fields: frozenset[str] | set[str]
) -> dict[str, Any]:
    """Decode multiple JSON TEXT columns in a SQLite row dict."""
    decoded = dict(row)
    for field in json_fields:
        if field in decoded:
            decoded[field] = json_decode(decoded[field])
    return decoded
