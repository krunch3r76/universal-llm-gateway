"""SQLite connection and migrations for trigger-schedule.sqlite."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from .migrations.migration_001_triggers import (
    MIGRATION_ID as MIGRATION_001_ID,
)
from .migrations.migration_001_triggers import (
    migrate as migrate_001,
)
from .migrations.migration_002_predicates import (
    MIGRATION_ID as MIGRATION_002_ID,
)
from .migrations.migration_002_predicates import (
    migrate as migrate_002,
)
from .migrations.migration_003_act_receipt import (
    MIGRATION_ID as MIGRATION_003_ID,
)
from .migrations.migration_003_act_receipt import (
    migrate as migrate_003,
)
from .migrations.migration_004_story_envelope import (
    MIGRATION_ID as MIGRATION_004_ID,
)
from .migrations.migration_004_story_envelope import (
    migrate as migrate_004,
)
from .migrations.migration_005_recur_idle import (
    MIGRATION_ID as MIGRATION_005_ID,
)
from .migrations.migration_005_recur_idle import (
    migrate as migrate_005,
)
from .migrations.migration_006_defer_observability import (
    MIGRATION_ID as MIGRATION_006_ID,
)
from .migrations.migration_006_defer_observability import (
    migrate as migrate_006,
)

logger = get_logger(__name__)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def as_utc(dt: datetime) -> datetime:
    """Normalize to UTC; treat naive datetimes as already-UTC (not local)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def db_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "trigger-schedule.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_file = path or db_path()
    conn = sqlite3.connect(str(db_file), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at REAL NOT NULL
        )
        """
    )
    applied = {
        row[0] for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
    }
    pending = [
        (MIGRATION_001_ID, migrate_001),
        (MIGRATION_002_ID, migrate_002),
        (MIGRATION_003_ID, migrate_003),
        (MIGRATION_004_ID, migrate_004),
        (MIGRATION_005_ID, migrate_005),
        (MIGRATION_006_ID, migrate_006),
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
        logger.info("trigger store applied migration %s", mig_id)
