"""migration_019: admit ``parent`` and ``window-binding`` on endpoint_wake_ledger.route."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_019"

_LEDGER_COLUMNS = (
    "id",
    "agent",
    "thread_id",
    "turn_id",
    "turn_number",
    "state",
    "wake_id",
    "route",
    "subscription_id",
    "cse_registration_id",
    "reason",
    "created_at",
    "claimed_at",
    "finished_at",
)


def run(conn: sqlite3.Connection) -> None:
    """Rebuild endpoint_wake_ledger so route CHECK includes parent and window-binding.

    SAVEPOINT m019, not BEGIN: ``connect()`` uses ``isolation_level=''`` and an
    earlier ``schema_migrations`` INSERT may already hold an implicit transaction.
    DDL outside a transaction autocommits, so a failed copy used to leave
    ``endpoint_wake_ledger_new`` behind and every later ``init_db`` failed on
    CREATE. Orphan ``turn_id`` values are omitted from the copy so
    ``foreign_keys=ON`` does not abort the rebuild.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'endpoint_wake_ledger'"
    ).fetchone()
    if row is None:
        return
    if "parent" in (row[0] or ""):
        return
    column_list = ", ".join(_LEDGER_COLUMNS)
    conn.execute("SAVEPOINT m019")
    try:
        conn.execute("DROP TABLE IF EXISTS endpoint_wake_ledger_new")
        conn.execute(
            """
            CREATE TABLE endpoint_wake_ledger_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                turn_number INTEGER NOT NULL,
                state TEXT NOT NULL CHECK (
                    state IN ('pending','claimed','delivered','refused','skipped','unknown')
                ),
                wake_id TEXT,
                route TEXT CHECK (route IN ('exact','wildcard','parent','window-binding','cse')),
                subscription_id INTEGER,
                cse_registration_id TEXT,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                claimed_at TEXT,
                finished_at TEXT,
                UNIQUE(agent, thread_id, turn_id)
            )
            """
        )
        conn.execute(
            "INSERT INTO endpoint_wake_ledger_new ("
            f"{column_list}) SELECT {column_list} FROM endpoint_wake_ledger "
            "WHERE turn_id IN (SELECT id FROM turns)"
        )
        conn.execute("DROP TABLE endpoint_wake_ledger")
        conn.execute(
            "ALTER TABLE endpoint_wake_ledger_new RENAME TO endpoint_wake_ledger"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_endpoint_wake_pair "
            "ON endpoint_wake_ledger(agent, thread_id, state)"
        )
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='pending', wake_id=NULL, claimed_at=NULL "
            "WHERE state='unknown' AND route IS NULL AND reason IS NULL AND finished_at IS NULL"
        )
        conn.execute("RELEASE m019")
    except BaseException:
        conn.execute("ROLLBACK TO m019")
        conn.execute("RELEASE m019")
        raise
