"""RootLedger sqlite connection — WAL mode, migration apply, sole-writer path."""

from __future__ import annotations

import os
import pwd
import sqlite3
import time
from pathlib import Path

from universal_logging import get_logger

from .migrations.migration_001_root_ledger import (
    MIGRATION_ID as MIGRATION_001_ID,
    migrate as migrate_001,
)
from .migrations.migration_002_conveyor_phase import (
    MIGRATION_ID as MIGRATION_002_ID,
    migrate as migrate_002,
)
from .migrations.migration_003_ledger_age import (
    MIGRATION_ID as MIGRATION_003_ID,
    migrate as migrate_003,
)
from .migrations.migration_004_propagation_ledger import (
    MIGRATION_ID as MIGRATION_004_ID,
    migrate as migrate_004,
)
from .migrations.migration_005_work_key_registry import (
    MIGRATION_ID as MIGRATION_005_ID,
    migrate as migrate_005,
)
from .migrations.migration_006_consult_orphan_drain import (
    MIGRATION_ID as MIGRATION_006_ID,
    migrate as migrate_006,
)
from .migrations.migration_007_settle_boundary import (
    MIGRATION_ID as MIGRATION_007_ID,
    migrate as migrate_007,
)

logger = get_logger(__name__)

_DISPATCH_HOME_MARKER = "cursor-dispatch-homes"
_WRITE_RETRIES = 3
_WRITE_BACKOFF_S = 0.05


def _operator_home() -> Path:
    """Real operator home — not cursor-sdk per-dispatch HOME swap."""
    if op_home := os.environ.get("CHARTER_RUNNER_OPERATOR_HOME"):
        return Path(op_home).expanduser()
    current = Path.home()
    if _DISPATCH_HOME_MARKER in current.as_posix():
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    return current


def charter_runner_data_dir() -> Path:
    """Stable charter-runner state dir shared by manage and dispatch contexts."""
    if override := os.environ.get("CHARTER_RUNNER_DATA_DIR"):
        return Path(override).expanduser()
    return _operator_home() / ".local" / "share" / "charter-runner"


def default_ledger_path() -> Path:
    return charter_runner_data_dir() / "root-ledger.sqlite"


def open_ledger_db(path: Path | None = None) -> sqlite3.Connection:
    """Open ledger db with WAL; create parent dir and apply migrations."""
    db_path = path or default_ledger_path()
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
    pending = [
        (MIGRATION_001_ID, migrate_001),
        (MIGRATION_002_ID, migrate_002),
        (MIGRATION_003_ID, migrate_003),
        (MIGRATION_004_ID, migrate_004),
        (MIGRATION_005_ID, migrate_005),
        (MIGRATION_006_ID, migrate_006),
        (MIGRATION_007_ID, migrate_007),
    ]
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
