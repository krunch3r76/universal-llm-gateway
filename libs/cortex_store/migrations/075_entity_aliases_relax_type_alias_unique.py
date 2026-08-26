"""Migration 075: drop ``UNIQUE (entity_type, alias)`` on ``entity_aliases``.

Fork 2 B4 — two live same-type entities may share a display name when
``duplicate_name_ok=true``; alias resolution returns 400 ambiguous instead of
blocking at INSERT.  Keeps ``PRIMARY KEY (entity_id, alias)`` and
``idx_entity_aliases_alias``.  Row-preserving table rebuild; idempotent when the
constraint is already absent.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.075")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _has_type_alias_unique(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='entity_aliases'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    normalized = " ".join(str(row[0]).split())
    return "UNIQUE (entity_type, alias)" in normalized


def migrate(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "entity_aliases"):
        logger.info("Migration 075: entity_aliases absent — skipping")
        return
    if not _has_type_alias_unique(conn):
        logger.info(
            "Migration 075: UNIQUE (entity_type, alias) already absent — skipping"
        )
        return

    row_count_before = conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP TABLE IF EXISTS entity_aliases_new")
        conn.execute(
            """
            CREATE TABLE entity_aliases_new (
                entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                entity_type TEXT NOT NULL,
                alias TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (entity_id, alias)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO entity_aliases_new (entity_id, entity_type, alias, created_at)
            SELECT entity_id, entity_type, alias, created_at FROM entity_aliases
            """
        )
        conn.execute("DROP TABLE entity_aliases")
        conn.execute("ALTER TABLE entity_aliases_new RENAME TO entity_aliases")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias "
            "ON entity_aliases(alias)"
        )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    row_count_after = conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0]
    if row_count_after != row_count_before:
        raise RuntimeError(
            "Migration 075: entity_aliases row count changed "
            f"({row_count_before} → {row_count_after})"
        )

    logger.info(
        "Migration 075: dropped UNIQUE (entity_type, alias); %d rows preserved",
        row_count_after,
    )
