"""migration_029: endpoint_wake_scan_drops — qualified-wake scan exits off the claim ledger."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_029"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS endpoint_wake_scan_drops ("
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "    agent TEXT NOT NULL,"
        "    thread_id TEXT NOT NULL,"
        "    turn_id INTEGER NOT NULL,"
        "    turn_number INTEGER,"
        "    rule_id TEXT,"
        "    reason TEXT NOT NULL,"
        "    subscription_id INTEGER,"
        "    scanned_at TEXT NOT NULL,"
        "    UNIQUE(agent, thread_id, turn_id, reason)"
        ")"
    )
