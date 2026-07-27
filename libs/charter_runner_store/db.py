"""RootLedger sqlite connection — WAL mode, migration apply, sole-writer path."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from universal_logging import get_logger

from .migrations.migration_001_root_ledger import MIGRATION_ID, migrate

logger = get_logger(__name__)

_DEFAULT_DIR = Path.home() / ".local" / "share" / "charter-runner"
_DEFAULT_PATH = _DEFAULT_DIR / "root-ledger.sqlite"
_WRITE_RETRIES = 3
_WRITE_BACKOFF_S = 0.05


def default_ledger_path() -> Path:
    return _DEFAULT_PATH


def open_ledger_db(path: Path | None = None) -> sqlite3.Connection:
    """Open ledger db with WAL; create parent dir and apply migrations."""
    db_path = path or _DEFAULT_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn)
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending migrations idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at REAL NOT NULL
        )
        """
    )
    applied = {
        row[0]
        for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }
    pending = [(MIGRATION_ID, migrate)]
    for mig_id, migrate_fn in pending:
        if mig_id in applied:
            continue
        migrate_fn(conn)
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (mig_id, time.time()),
        )
        conn.commit()
        logger.info("applied migration %s", mig_id)


def execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple | list = (),
) -> sqlite3.Cursor:
    """Bounded retry on sqlite OperationalError (contention class)."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_WRITE_RETRIES):
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "unable to open database file" not in str(exc).lower():
                raise
            time.sleep(_WRITE_BACKOFF_S * (attempt + 1))
    assert last_exc is not None
    raise last_exc
