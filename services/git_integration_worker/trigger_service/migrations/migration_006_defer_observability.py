"""Defer accounting, fleet verdict, degraded flag, coalesce skip count."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "006_defer_observability"

_DDL = """
ALTER TABLE triggers ADD COLUMN defer_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE triggers ADD COLUMN last_deferred_at TEXT;
ALTER TABLE triggers ADD COLUMN last_fleet_verdict TEXT;
ALTER TABLE triggers ADD COLUMN degraded INTEGER NOT NULL DEFAULT 0;
ALTER TABLE triggers ADD COLUMN last_coalesce_skipped INTEGER;
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
