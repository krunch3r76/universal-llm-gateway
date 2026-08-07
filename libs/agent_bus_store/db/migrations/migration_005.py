"""migration_005: Durable quiet-with-WIP alarm rows (A′ gate — arc 6885).

Adds ``thread_quiet_alarms`` so the watchdog quiet sweep can fire once per open
debt and stay idempotent across passes without abandoning the thread.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_005"


def run(conn: sqlite3.Connection) -> None:
    """Create ``thread_quiet_alarms`` for idempotent watchdog quiet firings.

    Columns mirror the architecture sketch: reason, wip_execution_ids, status.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS thread_quiet_alarms (
            alarm_id           TEXT PRIMARY KEY,
            thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            seat               TEXT NOT NULL,
            first_seen_at      TEXT NOT NULL,
            fired_at           TEXT NOT NULL,
            reason             TEXT NOT NULL
                CHECK (reason IN (
                    'wip_in_flight',
                    'closeout_unharvested',
                    'pickup_unbound'
                )),
            wip_execution_ids  TEXT NOT NULL DEFAULT '[]',
            status             TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'discharged', 'pager_escalated')),
            discharged_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_quiet_alarms_thread_status
            ON thread_quiet_alarms(thread_id, status);
        """
    )
