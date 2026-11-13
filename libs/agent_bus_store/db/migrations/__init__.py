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
    migration_010,
    migration_011,
    migration_012,
    migration_013,
    migration_014,
    migration_015,
    migration_016,
    migration_017,
    migration_018,
    migration_019,
    migration_020,
    migration_021,
    migration_022,
    migration_023,
    migration_024,
    migration_025,  # noqa: F401 — registry import
    migration_026,  # noqa: F401 — registry import
    migration_027,  # noqa: F401 — registry import
    migration_028,  # noqa: F401 — registry import
    migration_029,  # noqa: F401 — registry import
    migration_030,  # noqa: F401 — registry import
    migration_031,  # noqa: F401 — registry import
    migration_032,  # noqa: F401 — registry import
    migration_034,  # noqa: F401 — registry import
    migration_035,  # noqa: F401 — registry import
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
    migration_010,
    migration_011,
    migration_012,
    migration_013,
    migration_014,
    migration_015,
    migration_016,
    migration_017,
    migration_018,
    migration_019,
    migration_020,
    migration_021,
    migration_022,
    migration_023,
    migration_024,
    migration_025,
    migration_026,
    migration_027,
    migration_028,
    migration_029,
    migration_030,
    migration_031,
    migration_032,
    migration_034,
    migration_035,
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
