"""migration_023: chatgpt_work actuation columns on endpoint subscriptions."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_023"

_LEGACY_COLUMNS = (
    "id",
    "agent",
    "thread_scope",
    "state",
    "surface",
    "display",
    "title",
    "agent_id",
    "bound_by",
    "evidence",
    "created_at",
)


def _column_names(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(agent_endpoint_subscriptions)")]


def run(conn: sqlite3.Connection) -> None:
    """Rebuild subscriptions with chatgpt_work journal columns."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_endpoint_subscriptions'"
    ).fetchone()
    if row is None:
        return
    existing = _column_names(conn)
    if existing != list(_LEGACY_COLUMNS):
        raise RuntimeError(
            f"migration_023: unexpected agent_endpoint_subscriptions columns: {existing}"
        )
    if "actuation" in existing:
        return

    conn.execute("SAVEPOINT m023")
    try:
        conn.execute("DROP TABLE IF EXISTS agent_endpoint_subscriptions_new")
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
                scope_kind TEXT NOT NULL DEFAULT 'thread'
                    CHECK (scope_kind IN ('thread','agent')),
                actuation TEXT NULL
                    CHECK (actuation IS NULL OR actuation = 'chatgpt_work'),
                predecessor_id INTEGER NULL,
                home_house_id TEXT NULL,
                CHECK (thread_scope <> '*' OR surface IN ('grok','cse')),
                CHECK (
                    actuation IS NULL
                    OR (
                        actuation = 'chatgpt_work'
                        AND surface IS NULL
                        AND display IS NULL
                        AND scope_kind = 'agent'
                        AND (
                            (
                                agent = 'main-wake'
                                AND thread_scope = 'main-wake'
                                AND home_house_id IS NULL
                            )
                            OR (
                                agent <> 'main-wake'
                                AND thread_scope = agent
                                AND home_house_id IS NOT NULL
                            )
                        )
                    )
                )
            )
            """
        )
        old_count = conn.execute(
            "SELECT COUNT(*) FROM agent_endpoint_subscriptions"
        ).fetchone()[0]
        legacy_select = ", ".join(_LEGACY_COLUMNS)
        conn.execute(
            f"""
            INSERT INTO agent_endpoint_subscriptions_new ({legacy_select},
                scope_kind, actuation, predecessor_id, home_house_id)
            SELECT {legacy_select}, 'thread', NULL, NULL, NULL
            FROM agent_endpoint_subscriptions
            """
        )
        new_count = conn.execute(
            "SELECT COUNT(*) FROM agent_endpoint_subscriptions_new"
        ).fetchone()[0]
        if new_count != old_count:
            raise RuntimeError(
                f"migration_023: row count mismatch old={old_count} new={new_count}"
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
        conn.execute("RELEASE m023")
    except BaseException:
        try:
            conn.execute("ROLLBACK TO m023")
            conn.execute("RELEASE m023")
        except sqlite3.Error:
            pass
        raise
