"""Add recur_every_s for terminal-seam re-arm of recurring schedules."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "005_recur_idle"

_DDL = """
ALTER TABLE triggers ADD COLUMN recur_every_s INTEGER;
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
