"""Migration 083: FTS5 fulltext search on entities (S1 entities_fts).

Creates ``entities_fts`` with indexed name/aliases/slug/src_fname/description
columns and UNINDEXED entity_id/etype metadata. Maintained via
``reindex_entity_fts_conn`` on entity writes (no content-sync triggers).
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.083")


def migrate(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities_fts'"
    ).fetchone()
    if row is not None:
        logger.info("entities_fts already present — skipping create")
        return

    conn.execute(
        "CREATE VIRTUAL TABLE entities_fts USING fts5("
        "  name,"
        "  aliases,"
        "  slug,"
        "  src_fname,"
        "  description,"
        "  entity_id UNINDEXED,"
        "  etype UNINDEXED,"
        "  tokenize='porter unicode61'"
        ")"
    )
    logger.info("Created entities_fts FTS5 virtual table")

    from cortex_store.entity_fts import backfill_entities_fts_conn

    inserted = backfill_entities_fts_conn(conn)
    conn.commit()
    logger.info("Migration 083 (entities FTS5) complete — backfilled %d entities", inserted)
