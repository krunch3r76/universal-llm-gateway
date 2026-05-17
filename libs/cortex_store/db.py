from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("cortex-api.db")

_CORTEX_DB = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)
_TODOS_DB = Path(
    os.environ.get("TODOS_DB_PATH", str(Path.home() / ".cortex" / "todos.db"))
)
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# SQLite WAL allows concurrent readers but serializes writers. Without
# application-level coordination, thread-pool workers compete for the write
# lock through SQLite's polling busy-handler, causing unpredictable timeouts
# under burst load. This lock serializes writers with proper FIFO ordering.
WRITE_LOCK = threading.Lock()


def _connect(db_path: Path) -> sqlite3.Connection:
    # timeout: seconds to wait for a write lock under concurrent access.
    # Python default is 5s — 30s gives concurrent write ops room to complete.
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # SQLite has no built-in REGEXP — register one so callers can use
    # `col REGEXP pattern` in SQL. None inputs are treated as non-matching.
    conn.create_function(
        "REGEXP",
        2,
        lambda pattern, value: bool(
            re.search(pattern, str(value)) if value is not None else False
        ),
    )
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
            "json_decode: unparseable value %r (truncated), returning fallback",
            value[:80],
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


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """True when *table* exists in the connected SQLite database.

    Common pattern for graceful-degradation: app code that reads from a
    table introduced by a migration may run against a pre-migration
    database (test sandboxes, fresh installs). Callers can check
    ``table_exists`` and skip the read when the table is absent.

    Migrations should NOT import this helper — keep migration files
    self-contained for replay safety (a future refactor of this helper
    must not retroactively change how an old migration behaved).
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

_STMT_SPLIT = re.compile(r";\s*$", re.MULTILINE)
_COMMENT_LINE = re.compile(r"^\s*--.*$", re.MULTILINE)


def _parse_sql_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements, preserving multi-line ones."""
    stmts: list[str] = []
    for raw in _STMT_SPLIT.split(sql):
        cleaned = _COMMENT_LINE.sub("", raw).strip()
        if cleaned:
            stmts.append(cleaned)
    return stmts


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()


def _apply_sql_migration(conn: sqlite3.Connection, path: Path, version: int) -> int:
    """Execute a .sql migration file, tolerating duplicate-column ALTERs."""
    sql = path.read_text()
    statements = _parse_sql_statements(sql)
    skipped = 0
    for stmt in statements:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc):
                skipped += 1
                continue
            logger.error("Migration %03d failed on statement: %s", version, stmt)
            raise
    return skipped


def _apply_py_migration(conn: sqlite3.Connection, path: Path, version: int) -> None:
    """Execute a .py migration file by calling its ``migrate(conn)`` function."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"migration_{version:03d}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if not hasattr(module, "migrate"):
        raise AttributeError(
            f"Migration {path.name} missing required migrate(conn) function"
        )
    module.migrate(conn)


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations from the migrations/ directory.

    Supports ``.sql`` and ``.py`` migration files, both named ``NNN_description.ext``.
    SQL statements are executed individually so that idempotent ALTERs (which may
    hit "duplicate column name") don't block the rest of the migration.
    Python migrations must expose a ``migrate(conn)`` function.

    Returns the list of newly applied version numbers.
    """
    if not _MIGRATIONS_DIR.is_dir():
        logger.info("No migrations directory at %s — skipping", _MIGRATIONS_DIR)
        return []

    applied = _get_applied_versions(conn)
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    py_files = list(_MIGRATIONS_DIR.glob("*.py"))
    migration_files = sorted(sql_files + py_files, key=lambda p: p.name)
    newly_applied: list[int] = []

    for path in migration_files:
        match = re.match(r"^(\d+)", path.name)
        if not match:
            logger.warning("Skipping non-numbered migration file: %s", path.name)
            continue

        version = int(match.group(1))
        if version in applied:
            continue

        logger.info("Applying migration %03d: %s", version, path.name)

        skipped = 0
        if path.suffix == ".sql":
            skipped = _apply_sql_migration(conn, path, version)
        elif path.suffix == ".py":
            _apply_py_migration(conn, path, version)

        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, path.stem),
        )
        conn.commit()

        if skipped:
            logger.info(
                "Migration %03d applied (%d statements skipped — columns already exist)",
                version,
                skipped,
            )
        else:
            logger.info("Migration %03d applied successfully", version)
        newly_applied.append(version)

    return newly_applied
