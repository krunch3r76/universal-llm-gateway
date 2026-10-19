"""migration_016: endpoint subscriptions and the wake ledger."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_016"


def run(conn: sqlite3.Connection) -> None:
    """Create the append-only subscription journal and wake consumer tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_endpoint_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            thread_scope TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('subscribed','unsubscribed')),
            surface TEXT CHECK (surface IN ('grok','cursor')),
            display TEXT,
            title TEXT,
            agent_id TEXT,
            bound_by TEXT,
            evidence TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK (thread_scope <> '*' OR surface = 'grok')
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_endpoint_sub_current "
        "ON agent_endpoint_subscriptions(agent, thread_scope, id DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_wake_cursor (
            consumer TEXT PRIMARY KEY,
            high_water_turn_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS endpoint_wake_ledger (
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
        "CREATE INDEX IF NOT EXISTS idx_endpoint_wake_pair "
        "ON endpoint_wake_ledger(agent, thread_id, state)"
    )
