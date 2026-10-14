"""migration_017: admit ``cse`` surface on endpoint subscriptions."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_017"


def run(conn: sqlite3.Connection) -> None:
    """Rebuild subscriptions with ``cse`` in surface and wildcard checks."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_endpoint_subscriptions'"
    ).fetchone()
    if row is None:
        return
    conn.execute(
        """
        CREATE TABLE agent_endpoint_subscriptions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            thread_scope TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('subscribed','unsubscribed')),
            surface TEXT CHECK (surface IN ('grok','cursor','cse')),
            display TEXT,
            title TEXT,
            agent_id TEXT,
            bound_by TEXT,
            evidence TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK (thread_scope <> '*' OR surface IN ('grok','cse'))
        )
        """
    )
    conn.execute(
        """
        INSERT INTO agent_endpoint_subscriptions_new
        SELECT id, agent, thread_scope, state, surface, display, title, agent_id,
               bound_by, evidence, created_at
        FROM agent_endpoint_subscriptions
        """
    )
    conn.execute("DROP TABLE agent_endpoint_subscriptions")
    conn.execute(
        "ALTER TABLE agent_endpoint_subscriptions_new "
        "RENAME TO agent_endpoint_subscriptions"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_endpoint_sub_current "
        "ON agent_endpoint_subscriptions(agent, thread_scope, id DESC)"
    )
