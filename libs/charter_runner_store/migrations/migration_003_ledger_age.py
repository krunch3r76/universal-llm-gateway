"""Migration 003: ledger-resident age columns + belt age_clock table (M1/M4)."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.003")

MIGRATION_ID = "migration_003_ledger_age"

_AGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("demand_since", "REAL"),
    ("demand_observation_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_fire_attempt_at", "REAL"),
    ("first_refuse_at", "REAL"),
    ("refuse_streak", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(root_ledger)").fetchall()
    }
    for name, ddl in _AGE_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE root_ledger ADD COLUMN {name} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS age_clock (
          clock_class TEXT NOT NULL,
          clock_key TEXT NOT NULL,
          first_seen_at REAL,
          observation_count INTEGER NOT NULL DEFAULT 0,
          birth REAL,
          PRIMARY KEY (clock_class, clock_key)
        )
        """
    )
    conn.commit()
    logger.info("migration 003: ledger age columns + age_clock ready")
