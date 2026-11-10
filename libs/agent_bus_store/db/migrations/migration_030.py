"""migration_030: refuse backward endpoint_wake_cursor.high_water_turn_id writes."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_030"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS endpoint_wake_cursor_no_rewind
        BEFORE UPDATE OF high_water_turn_id ON endpoint_wake_cursor
        WHEN NEW.high_water_turn_id < OLD.high_water_turn_id
        BEGIN
            SELECT RAISE(ABORT, 'endpoint_wake_cursor: backward high_water write refused (friction a:43532)');
        END;
        """
    )
