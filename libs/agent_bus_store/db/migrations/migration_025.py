"""migration_025: admit ``remapped`` on endpoint_wake_ledger.route."""

from __future__ import annotations

import logging
import sqlite3

MIGRATION_ID = "migration_025"
logger = logging.getLogger(__name__)

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
    "decided_by_rule",
)


def run(conn: sqlite3.Connection) -> None:
    """Rebuild endpoint_wake_ledger so route CHECK includes remapped."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'endpoint_wake_ledger'"
    ).fetchone()
    if row is None:
        return
    ddl = row[0] or ""
    if "'remapped'" in ddl:
        return
    existing_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(endpoint_wake_ledger)")
    }
    unknown = existing_cols - set(_LEDGER_COLUMNS)
    if unknown:
        raise RuntimeError(
            f"migration_025: unexpected endpoint_wake_ledger columns: {sorted(unknown)}"
        )
    column_list = ", ".join(_LEDGER_COLUMNS)
    conn.execute("SAVEPOINT m025")
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
                route TEXT CHECK (route IN (
                    'exact','wildcard','parent','window-binding','cse','agent_id','remapped'
                )),
                subscription_id INTEGER,
                cse_registration_id TEXT,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                claimed_at TEXT,
                finished_at TEXT,
                decided_by_rule TEXT,
                UNIQUE(agent, thread_id, turn_id)
            )
            """
        )
        orphan_count = conn.execute(
            "SELECT count(*) FROM endpoint_wake_ledger "
            "WHERE turn_id NOT IN (SELECT id FROM turns)"
        ).fetchone()[0]
        if orphan_count > 0:
            logger.warning(
                "migration_025 omitting %s endpoint_wake_ledger rows whose turn_id is not in turns",
                orphan_count,
            )
        else:
            logger.debug(
                "migration_025 omitting 0 endpoint_wake_ledger rows whose turn_id is not in turns"
            )
        select_cols = [
            c if c in existing_cols else "NULL AS " + c for c in _LEDGER_COLUMNS
        ]
        select_list = ", ".join(select_cols)
        conn.execute(
            "INSERT INTO endpoint_wake_ledger_new ("
            f"{column_list}) SELECT {select_list} FROM endpoint_wake_ledger "
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
        conn.execute("RELEASE m025")
    except BaseException:
        try:
            conn.execute("ROLLBACK TO m025")
            conn.execute("RELEASE m025")
        except sqlite3.Error:
            pass
        raise
