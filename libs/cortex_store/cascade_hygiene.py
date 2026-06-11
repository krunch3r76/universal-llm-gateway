"""Reap consistency and FK orphan maintenance for cortex_store.

Soft-reap consistency (deactivate relationships, drop salience cache) and an
explicit idempotent orphan sweep. This is **not** a hard-delete cascade —
assertion/entity row removal still needs a full ``_ENTITY_ID_REFERENCES`` design.
"""

from __future__ import annotations

import sqlite3

from .db import table_exists


def apply_reap_consistency_hygiene(
    conn: sqlite3.Connection, entity_id: str, now_iso: str
) -> dict[str, int]:
    """Soft-reap consistency: deactivate touching relationships, drop salience."""
    rel = 0
    if table_exists(conn, "relationships"):
        rel = conn.execute(
            "UPDATE relationships SET active = 0, updated_at = ? "
            "WHERE active = 1 AND (from_entity = ? OR to_entity = ?)",
            (now_iso, entity_id, entity_id),
        ).rowcount
    sal = 0
    if table_exists(conn, "entity_salience_cache"):
        sal = conn.execute(
            "DELETE FROM entity_salience_cache WHERE entity_id = ?", (entity_id,)
        ).rowcount
    return {"relationships_deactivated": rel, "salience_rows_dropped": sal}


def purge_fk_orphans(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete orphaned child rows whose FK parent entity/assertion is gone."""
    counts: dict[str, int] = {}
    counts["relationships"] = conn.execute(
        "DELETE FROM relationships WHERE from_entity NOT IN (SELECT id FROM entities) "
        "OR to_entity NOT IN (SELECT id FROM entities)"
    ).rowcount
    counts["near_duplicate_flags"] = conn.execute(
        "DELETE FROM near_duplicate_flags "
        "WHERE assertion_id NOT IN (SELECT id FROM assertions) "
        "OR duplicate_of NOT IN (SELECT id FROM assertions)"
    ).rowcount
    counts["entity_salience_cache"] = conn.execute(
        "DELETE FROM entity_salience_cache "
        "WHERE entity_id NOT IN (SELECT id FROM entities)"
    ).rowcount
    counts["tag_assignments"] = conn.execute(
        "DELETE FROM tag_assignments WHERE entity_id NOT IN (SELECT id FROM entities) "
        "OR assertion_id NOT IN (SELECT id FROM assertions)"
    ).rowcount
    return counts
