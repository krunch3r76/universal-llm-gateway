"""Migration 039: normalization-decision ledger (v1.3.1 instrumentation).

Adds 5 columns to `assertions` for shadow-mode cardinality decision capture:
  - predicate_form (TEXT, normalized predicate projection — peer column per v2.4 §6.7)
  - raw_predicate_form (TEXT, caller-supplied pre-normalize form)
  - normalization_decision (TEXT, enum: resolved_single | no_match | collision_refused | ...)
  - candidate_set_fingerprint (TEXT, SHA256 first-16 of sorted candidates)
  - normalizer_version (TEXT, e.g. "v1.3.1")

Two partial indices for efficient Path-2 detector and Path-3 audit-gate queries.

Idempotent: guarded by _column_exists (from 037 pattern). No data backfill;
pre-v1.3.1 rows remain NULL on these columns (detector/audit filter them out).

Drives decision:cortex-alias-drift-defense-in-depth (assertion 10404).
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.039")


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row[1]) == column for row in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _add_normalization_ledger_columns(conn: sqlite3.Connection) -> None:
    """Add the 4 ledger columns + 2 partial indices (idempotent)."""
    if not _table_exists(conn, "assertions"):
        logger.info("assertions table not present yet — skipping ledger columns")
        return

    cols_to_add = [
        ("predicate_form", "TEXT"),
        ("raw_predicate_form", "TEXT"),
        ("normalization_decision", "TEXT"),
        ("candidate_set_fingerprint", "TEXT"),
        ("normalizer_version", "TEXT"),
    ]

    for col_name, col_type in cols_to_add:
        if not _column_exists(conn, "assertions", col_name):
            conn.execute(f"ALTER TABLE assertions ADD COLUMN {col_name} {col_type}")

    # Partial indices — only on non-NULL rows (post-v1.3.1 writes that seeded predicate_form)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assertions_normalization_decision "
        "ON assertions(normalization_decision) "
        "WHERE normalization_decision IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assertions_raw_predicate_form "
        "ON assertions(raw_predicate_form) "
        "WHERE raw_predicate_form IS NOT NULL"
    )


def migrate(conn: sqlite3.Connection) -> None:
    _add_normalization_ledger_columns(conn)
