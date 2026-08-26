"""Migration 075: drop ``UNIQUE (entity_type, alias)`` on ``entity_aliases``.

Fork 2 B4 — two live same-type entities may share a display name when
``duplicate_name_ok=true``; alias resolution returns 400 ambiguous instead of
blocking at INSERT.  Keeps ``PRIMARY KEY (entity_id, alias)`` and
``idx_entity_aliases_alias``.  Row-preserving table rebuild; idempotent when the
constraint is already absent.
"""

from __future__ import annotations

import sqlite3

from cortex_store.entity_aliases import has_type_alias_unique, rebuild_entity_aliases
from universal_logging import get_logger

logger = get_logger("cortex-api.migration.075")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "entity_aliases"):
        logger.info("Migration 075: entity_aliases absent — skipping")
        return
    if not has_type_alias_unique(conn):
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

        row_count_copied = conn.execute(
            "SELECT COUNT(*) FROM entity_aliases_new"
        ).fetchone()[0]
        if row_count_copied != row_count_before:
            raise RuntimeError(
                "Migration 075: entity_aliases copy row count changed "
                f"({row_count_before} → {row_count_copied})"
            )

        conn.execute("DROP TABLE entity_aliases")
        conn.execute("ALTER TABLE entity_aliases_new RENAME TO entity_aliases")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias "
            "ON entity_aliases(alias)"
        )

        conn.commit()

        conn.execute("PRAGMA foreign_keys=ON")

        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(
                "Migration 075: foreign_key_check failed: "
                f"{fk_violations[:20]}"
            )

        report = rebuild_entity_aliases(conn)

        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if fk_on != 1:
            raise RuntimeError(
                "Migration 075: PRAGMA foreign_keys != 1 after migrate"
            )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")

    logger.info(
        "Migration 075: dropped UNIQUE (entity_type, alias); "
        "rebuild inserted %d rows",
        report.row_count,
    )
    if report.residual_collisions:
        logger.warning(
            "Migration 075: %d cross-entity alias collision(s) restored: %s",
            len(report.residual_collisions),
            report.residual_collisions,
        )
