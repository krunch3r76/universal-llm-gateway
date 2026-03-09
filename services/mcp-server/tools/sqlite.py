"""SQLite tools — read/write access to configured SQLite databases.

Databases are registered in a YAML config file mapped by logical names.
Config path: SQLITE_CONFIG_PATH env var (default /data/sqlite-config.yaml).
Database files: stored under configured paths (typically /data/databases/).

Safety:
  - sqlite_query: SELECT-only, parameterized, row-limited
  - sqlite_execute: blocks DROP/PRAGMA unless allow_destructive is set
  - All value bindings use SQLite parameterization
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(os.getenv("SQLITE_CONFIG_PATH", "/data/sqlite-config.yaml"))
_DEFAULT_MAX_ROWS = 100
_DEFAULT_ALLOW_DESTRUCTIVE = False
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(DROP\s+(TABLE|DATABASE|INDEX|VIEW|TRIGGER)|PRAGMA)\b",
    re.IGNORECASE,
)
_SELECT_PATTERN = re.compile(r"^\s*SELECT\b", re.IGNORECASE)

_SEED_SCHEMA = """\
CREATE TABLE IF NOT EXISTS mcp_tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    description TEXT,
    parameters TEXT,
    when_to_use TEXT,
    when_not_to_use TEXT,
    gotchas TEXT,
    sandbox TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT,
    relationship TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    date TEXT NOT NULL,
    category TEXT,
    urgency TEXT,
    notes TEXT,
    resolved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT,
    lesson TEXT NOT NULL,
    context TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _default_config() -> dict[str, Any]:
    return {
        "databases": {},
        "max_rows": _DEFAULT_MAX_ROWS,
        "allow_destructive": _DEFAULT_ALLOW_DESTRUCTIVE,
    }


def _load_config() -> dict[str, Any]:
    """Load SQLite config from YAML file. Returns defaults if file is missing."""
    if not _CONFIG_PATH.exists():
        logger.warning(
            "SQLite config not found at %s; using defaults (no databases configured)",
            _CONFIG_PATH,
        )
        return _default_config()

    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Failed to load SQLite config from %s: %s", _CONFIG_PATH, exc)
        return _default_config()

    sqlite_section = raw.get("sqlite", raw)
    return {
        "databases": sqlite_section.get("databases", {}),
        "max_rows": sqlite_section.get("max_rows", _DEFAULT_MAX_ROWS),
        "allow_destructive": sqlite_section.get(
            "allow_destructive", _DEFAULT_ALLOW_DESTRUCTIVE
        ),
    }


_CONFIG = _load_config()


def _resolve_db_path(db_name: str) -> Path | None:
    """Map a logical database name to its file path from config."""
    db_entry = _CONFIG["databases"].get(db_name)
    if db_entry is None:
        return None
    raw_path = db_entry if isinstance(db_entry, str) else db_entry.get("path", "")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    return Path(os.path.expanduser(raw_path))


def _seed_default_db(db_path: Path) -> None:
    """Create seed tables in the default database if it doesn't exist yet."""
    if db_path.exists():
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Creating default database with seed schema at %s", db_path)
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.executescript(_SEED_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
        record("mcp.sqlite.seed.created", path=str(db_path))
    except sqlite3.Error as exc:
        logger.error("Failed to create seed database at %s: %s", db_path, exc)


def register_sqlite_tools(mcp: FastMCP) -> None:
    """Register SQLite tools on *mcp* and seed the default database if needed."""
    default_path = _resolve_db_path("default")
    if default_path is not None:
        _seed_default_db(default_path)

    max_rows: int = int(_CONFIG["max_rows"])
    allow_destructive: bool = bool(_CONFIG["allow_destructive"])

    @mcp.tool()
    def sqlite_list_databases() -> dict[str, list[dict[str, str]]]:
        """List all configured SQLite databases."""
        databases: list[dict[str, str]] = []
        for name, entry in _CONFIG["databases"].items():
            if isinstance(entry, str):
                path, description = entry, ""
            else:
                path = str(entry.get("path", ""))
                description = str(entry.get("description", ""))
            databases.append(
                {
                    "name": str(name),
                    "path": path,
                    "description": description,
                }
            )

        record("mcp.sqlite.databases.listed", count=len(databases))
        return {"databases": databases}

    @mcp.tool()
    def sqlite_schema(
        db: str = "default",
        table: str | None = None,
    ) -> dict[str, Any]:
        """Introspect database schema — list tables and their columns."""
        db_path = _resolve_db_path(db)
        if db_path is None:
            return {
                "error": (
                    f"Unknown database {db!r}. "
                    "Use sqlite_list_databases to see available databases."
                )
            }
        if not db_path.exists():
            return {"error": f"Database file does not exist: {db!r}"}

        if table and not _IDENTIFIER_PATTERN.fullmatch(table):
            return {"error": "Invalid table name. Use letters, numbers, and underscore."}

        t0 = monotonic_now()
        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                if table:
                    cursor.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    )
                    if cursor.fetchone() is None:
                        return {"error": f"Table not found: {table}"}
                    tables_to_inspect = [table]
                else:
                    cursor.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY name"
                    )
                    tables_to_inspect = [str(row["name"]) for row in cursor.fetchall()]

                result_tables: list[dict[str, Any]] = []
                for tbl in tables_to_inspect:
                    cursor.execute(f'PRAGMA table_info("{tbl}")')
                    columns: list[dict[str, Any]] = []
                    for col in cursor.fetchall():
                        columns.append(
                            {
                                "name": col["name"],
                                "type": col["type"],
                                "pk": bool(col["pk"]),
                                "nullable": not bool(col["notnull"]),
                            }
                        )
                    result_tables.append({"name": tbl, "columns": columns})
        except sqlite3.Error as exc:
            record("mcp.sqlite.schema.failed", db=db, error=str(exc))
            return {"error": f"Schema inspection failed: {exc}"}

        duration = monotonic_now() - t0
        record(
            "mcp.sqlite.schema.inspected",
            db=db,
            table=table or "*",
            table_count=len(result_tables),
            duration_s=round(duration, 3),
        )
        logger.info("sqlite_schema: db=%s table=%s -> %d tables", db, table, len(result_tables))
        return {"tables": result_tables}

    @mcp.tool()
    def sqlite_query(
        sql: str,
        db: str = "default",
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a read-only SELECT query against a SQLite database."""
        db_path = _resolve_db_path(db)
        if db_path is None:
            return {
                "error": (
                    f"Unknown database {db!r}. "
                    "Use sqlite_list_databases to see available databases."
                )
            }
        if not db_path.exists():
            return {"error": f"Database file does not exist: {db!r}"}
        if not _SELECT_PATTERN.match(sql):
            return {
                "error": (
                    "Only SELECT statements are allowed in sqlite_query. "
                    "Use sqlite_execute for writes."
                )
            }

        t0 = monotonic_now()
        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(sql, params or [])
                rows_raw = cursor.fetchmany(max_rows)
                columns = (
                    [str(desc[0]) for desc in cursor.description]
                    if cursor.description
                    else []
                )
                rows = [list(row) for row in rows_raw]
        except sqlite3.Error as exc:
            record("mcp.sqlite.query.failed", db=db, error=str(exc))
            return {"error": f"Query failed: {exc}"}

        duration = monotonic_now() - t0
        record(
            "mcp.sqlite.query.executed",
            db=db,
            row_count=len(rows),
            duration_s=round(duration, 3),
        )
        logger.info("sqlite_query: db=%s -> %d rows (%.3fs)", db, len(rows), duration)
        return {"columns": columns, "rows": rows, "count": len(rows)}

    @mcp.tool()
    def sqlite_execute(
        sql: str,
        db: str = "default",
        params: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a write statement against a SQLite database."""
        db_path = _resolve_db_path(db)
        if db_path is None:
            return {
                "error": (
                    f"Unknown database {db!r}. "
                    "Use sqlite_list_databases to see available databases."
                )
            }
        if not db_path.exists():
            return {"error": f"Database file does not exist: {db!r}"}
        if _SELECT_PATTERN.match(sql):
            return {"error": "SELECT statements should use sqlite_query, not sqlite_execute."}

        if not allow_destructive and _DESTRUCTIVE_PATTERN.search(sql):
            record("mcp.sqlite.execute.blocked", db=db, reason="destructive_statement")
            return {
                "error": (
                    "Destructive statements (DROP, PRAGMA) are blocked. "
                    "Set allow_destructive: true in the SQLite config to enable."
                )
            }

        t0 = monotonic_now()
        try:
            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params or [])
                conn.commit()
                rows_affected = cursor.rowcount
                last_insert_id = int(cursor.lastrowid or 0)
        except sqlite3.Error as exc:
            record("mcp.sqlite.execute.failed", db=db, error=str(exc))
            return {"error": f"Execute failed: {exc}"}

        duration = monotonic_now() - t0
        record(
            "mcp.sqlite.execute.completed",
            db=db,
            rows_affected=rows_affected,
            duration_s=round(duration, 3),
        )
        logger.info(
            "sqlite_execute: db=%s -> %d rows affected (%.3fs)",
            db,
            rows_affected,
            duration,
        )
        return {"rows_affected": rows_affected, "last_insert_id": last_insert_id}
