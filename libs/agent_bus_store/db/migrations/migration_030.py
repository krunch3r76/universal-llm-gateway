"""migration_030: refuse backward endpoint_wake_cursor.high_water_turn_id writes."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_030"

_INSERT_ABORT_MSG = (
    "endpoint_wake_cursor: backward high_water insert refused (friction a:43532)"
)
_UPDATE_ABORT_MSG = (
    "endpoint_wake_cursor: backward high_water write refused (friction a:43532)"
)


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS endpoint_wake_cursor_no_rewind
        BEFORE UPDATE OF high_water_turn_id ON endpoint_wake_cursor
        WHEN NEW.high_water_turn_id < OLD.high_water_turn_id
        BEGIN
            SELECT RAISE(ABORT, '{_UPDATE_ABORT_MSG}');
        END;
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS endpoint_wake_cursor_no_rewind_insert
        BEFORE INSERT ON endpoint_wake_cursor
        WHEN EXISTS (
            SELECT 1 FROM endpoint_wake_cursor AS old
            WHERE old.consumer = NEW.consumer
              AND old.high_water_turn_id > NEW.high_water_turn_id
        )
        BEGIN
            SELECT RAISE(ABORT, '{_INSERT_ABORT_MSG}');
        END;
        """
    )
