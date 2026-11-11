"""migration_031: append-only thread_seat_ledger (Phase 2 shadow SoT)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_031"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_seat_ledger (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id     TEXT    NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            epoch         INTEGER NOT NULL,
            transition    TEXT    NOT NULL CHECK (transition IN (
                'birth','hop','rebind','close','backfill'
            )),
            state         TEXT    NOT NULL CHECK (state IN ('open','closed','contested')),
            chat_id       TEXT,
            chat_url      TEXT,
            prev_chat_id  TEXT,
            actor         TEXT    NOT NULL,
            evidence      TEXT,
            request_id    TEXT    NOT NULL UNIQUE,
            created_at    TEXT    NOT NULL,
            UNIQUE (thread_id, epoch)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thread_seat_ledger_chat "
        "ON thread_seat_ledger(chat_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thread_seat_ledger_thread_epoch "
        "ON thread_seat_ledger(thread_id, epoch DESC)"
    )
