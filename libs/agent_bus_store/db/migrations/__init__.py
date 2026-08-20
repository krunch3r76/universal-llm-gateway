"""Numbered migration registry for the agent_bus_store SQLite schema.

Add new migrations by appending to MIGRATIONS. The runner is idempotent:
running it twice on the same DB produces no errors and no duplicate rows.
"""

from __future__ import annotations

import sqlite3

from . import (
    migration_001,
    migration_002,
    migration_003,
    migration_004,
    migration_005,
    migration_006,
    migration_007,
    migration_008,
    migration_009,
)

# Ordered list — append new migration modules here.
MIGRATIONS = [
    migration_001,
    migration_002,
    migration_003,
    migration_004,
    migration_005,
    migration_006,
    migration_007,
    migration_008,
    migration_009,
]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "    id         TEXT PRIMARY KEY,"
        "    applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any unapplied migrations in MIGRATIONS order.

    Skips migrations already recorded in schema_migrations. The table
    is created here if it does not yet exist (self-bootstrapping).
    """
    _ensure_migrations_table(conn)
    for migration in MIGRATIONS:
        migration_id = migration.MIGRATION_ID
        row = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = ?", (migration_id,)
        ).fetchone()
        if row is None:
            migration.run(conn)
            conn.execute(
                "INSERT INTO schema_migrations (id) VALUES (?)", (migration_id,)
            )
