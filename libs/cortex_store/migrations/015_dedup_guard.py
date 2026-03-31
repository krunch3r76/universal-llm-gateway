"""Migration 015: Dedup guard schema.

Adds idempotent-write infrastructure per tasks/specs/cortex-dedup-guard.md:
  - assertions.claim_hash + partial unique index (active assertions only)
  - relationships.active + relationships.superseded_by + partial unique index
  - assertions_fts (FTS5 content-sync table + triggers)
  - extraction_runs table

Deduplicates existing data before creating unique indexes.
"""

from __future__ import annotations

import logging
import sqlite3

from cortex_store.claim_hash import compute_claim_hash

logger = logging.getLogger("cortex-api.migration.015")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def migrate(conn: sqlite3.Connection) -> None:
    # ---------------------------------------------------------------
    # 1. Add claim_hash column to assertions
    # ---------------------------------------------------------------
    try:
        conn.execute("ALTER TABLE assertions ADD COLUMN claim_hash TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise

    # ---------------------------------------------------------------
    # 2. Compute claim_hash for all existing rows
    # ---------------------------------------------------------------
    rows = conn.execute("SELECT id, entity_id, claim FROM assertions").fetchall()
    updated = 0
    for row in rows:
        aid, entity_id, claim = row[0], row[1], row[2]
        if entity_id and claim:
            h = compute_claim_hash(entity_id, claim)
            conn.execute("UPDATE assertions SET claim_hash = ? WHERE id = ?", (h, aid))
            updated += 1
    logger.info("Computed claim_hash for %d assertions", updated)

    # ---------------------------------------------------------------
    # 3. Deduplicate active assertions by (entity_id, claim_hash)
    # ---------------------------------------------------------------
    dupes = conn.execute(
        "SELECT entity_id, claim_hash, GROUP_CONCAT(id) AS ids, COUNT(*) AS cnt "
        "FROM assertions "
        "WHERE superseded_by IS NULL AND claim_hash IS NOT NULL "
        "GROUP BY entity_id, claim_hash "
        "HAVING cnt > 1"
    ).fetchall()

    superseded_count = 0
    for row in dupes:
        ids = sorted(int(x) for x in row[2].split(","))
        keeper = ids[0]
        for dup_id in ids[1:]:
            conn.execute(
                "UPDATE assertions SET superseded_by = ?, review_status = 'rejected' "
                "WHERE id = ?",
                (keeper, dup_id),
            )
            superseded_count += 1
    logger.info(
        "Deduplicated assertions: %d clusters, %d rows superseded",
        len(dupes),
        superseded_count,
    )

    # ---------------------------------------------------------------
    # 4. Partial unique index on active assertions
    # ---------------------------------------------------------------
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_assertions_claim_dedup "
        "ON assertions(entity_id, claim_hash) "
        "WHERE superseded_by IS NULL"
    )

    # ---------------------------------------------------------------
    # 5. Add active + superseded_by to relationships
    # ---------------------------------------------------------------
    for col_sql in [
        "ALTER TABLE relationships ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1",
        "ALTER TABLE relationships ADD COLUMN superseded_by INTEGER REFERENCES relationships(id)",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise

    # ---------------------------------------------------------------
    # 6. Deduplicate active relationships by (from_entity, to_entity, type)
    # ---------------------------------------------------------------
    rel_dupes = conn.execute(
        "SELECT from_entity, to_entity, type, GROUP_CONCAT(id) AS ids, COUNT(*) AS cnt "
        "FROM relationships "
        "WHERE active = 1 "
        "GROUP BY from_entity, to_entity, type "
        "HAVING cnt > 1"
    ).fetchall()

    rel_superseded = 0
    for row in rel_dupes:
        ids = sorted(int(x) for x in row[3].split(","))
        keeper = ids[0]
        for dup_id in ids[1:]:
            conn.execute(
                "UPDATE relationships SET active = 0, superseded_by = ? WHERE id = ?",
                (keeper, dup_id),
            )
            rel_superseded += 1
    logger.info(
        "Deduplicated relationships: %d clusters, %d rows deactivated",
        len(rel_dupes),
        rel_superseded,
    )

    # ---------------------------------------------------------------
    # 7. Partial unique index on active relationships
    # ---------------------------------------------------------------
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_active_dedup "
        "ON relationships(from_entity, to_entity, type) "
        "WHERE active = 1"
    )

    # ---------------------------------------------------------------
    # 8. FTS5 content-sync table for assertions
    # ---------------------------------------------------------------
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS assertions_fts USING fts5("
        "  claim,"
        "  entity_id UNINDEXED,"
        "  content=assertions,"
        "  content_rowid=id"
        ")"
    )

    conn.execute(
        "INSERT INTO assertions_fts(rowid, claim, entity_id) "
        "SELECT id, claim, entity_id FROM assertions "
        "WHERE superseded_by IS NULL"
    )

    fts_count = conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE superseded_by IS NULL"
    ).fetchone()[0]
    logger.info("Backfilled assertions_fts with %d active assertions", fts_count)

    # Content-sync triggers: keep FTS index aligned with assertion lifecycle
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS assertions_fts_insert "
        "AFTER INSERT ON assertions "
        "WHEN NEW.superseded_by IS NULL "
        "BEGIN "
        "  INSERT INTO assertions_fts(rowid, claim, entity_id) "
        "  VALUES (NEW.id, NEW.claim, NEW.entity_id); "
        "END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS assertions_fts_supersede "
        "AFTER UPDATE OF superseded_by ON assertions "
        "WHEN NEW.superseded_by IS NOT NULL AND OLD.superseded_by IS NULL "
        "BEGIN "
        "  INSERT INTO assertions_fts(assertions_fts, rowid, claim, entity_id) "
        "  VALUES ('delete', OLD.id, OLD.claim, OLD.entity_id); "
        "END"
    )

    # ---------------------------------------------------------------
    # 9. Extraction runs table (performance optimization for Phase 4)
    # ---------------------------------------------------------------
    extraction_run_columns = _table_columns(conn, "extraction_runs")
    if not extraction_run_columns:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS extraction_runs ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  source_uri TEXT NOT NULL,"
            "  content_hash TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'registered'"
            "    CHECK(status IN ('registered', 'completed', 'failed')),"
            "  assertion_count INTEGER DEFAULT 0,"
            "  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),"
            "  completed_at TEXT"
            ")"
        )
    elif "content_hash" not in extraction_run_columns:
        # Older clean-slate DBs already created extraction_runs without content_hash.
        conn.execute("ALTER TABLE extraction_runs ADD COLUMN content_hash TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_extraction_runs_source "
        "ON extraction_runs(source_uri, content_hash)"
    )

    logger.info("Migration 015 (dedup guard schema) complete")
