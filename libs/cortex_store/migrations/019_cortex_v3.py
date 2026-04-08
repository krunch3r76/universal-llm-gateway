"""Migration 019: Cortex v3 — Kumiho grounding.

Adds BYO-storage and consolidation enrichment columns to assertions:
  - prospective_summary (LLM-generated future-scenario implications)
  - events_json (structured events with consequences)
  - artifact_uri (pointer to external payload)
  - artifact_storage (inline/local/rag/arkiv)

Adds Kumiho-aligned edge types:
  - derived_from (asset provenance tracking)
  - depends_on (dependency tracking for AnalyzeImpact traversal)

Origin: Agent bus thread 435, cortex-v3-spec.md
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("cortex-api.migration.019")


def migrate(conn: sqlite3.Connection) -> None:
    # ---------------------------------------------------------------
    # 1. Add v3 columns to assertions
    # ---------------------------------------------------------------
    new_columns = [
        ("prospective_summary", "TEXT"),
        ("events_json", "TEXT"),
        ("artifact_uri", "TEXT"),
        ("artifact_storage", "TEXT NOT NULL DEFAULT 'inline'"),
    ]

    for col_name, col_type in new_columns:
        try:
            conn.execute(
                f"ALTER TABLE assertions ADD COLUMN {col_name} {col_type}"
            )
            logger.info("Added column: assertions.%s", col_name)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc):
                logger.info("Column already exists: assertions.%s", col_name)
            else:
                raise

    # ---------------------------------------------------------------
    # 2. Add Kumiho-aligned edge types
    # ---------------------------------------------------------------
    edge_types = [
        ("derived_from", "from_node was derived from or produced using to_node", 1),
        ("depends_on", "from_node depends on to_node being current/valid", 1),
    ]

    for edge_type, description, directional in edge_types:
        conn.execute(
            "INSERT OR IGNORE INTO session_edge_types (type, description, directional) "
            "VALUES (?, ?, ?)",
            (edge_type, description, directional),
        )
        logger.info("Ensured edge type: %s", edge_type)

    # ---------------------------------------------------------------
    # 3. Partial index for non-inline artifact lookups
    # ---------------------------------------------------------------
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assertions_artifact_storage "
        "ON assertions(artifact_storage) WHERE artifact_storage != 'inline'"
    )

    logger.info("Migration 019 (Cortex v3 — Kumiho grounding) complete")
