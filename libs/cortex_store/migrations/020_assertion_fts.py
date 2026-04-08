"""Migration 020: FTS5 fulltext search on assertions.

Creates an ``assertions_fts`` FTS5 virtual table with a composite
``indexed_text`` column (claim + prospective_summary + flattened events +
entity_id).  ``assertion_id`` and ``entity_id`` are UNINDEXED metadata
columns for filtering without inflating the token index.

Backfills all existing assertions at migration time.

Origin: Agent bus thread 437 — Kumiho enrichment retrieval surface.
"""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger("cortex-api.migration.020")


def _build_indexed_text(
    claim: str,
    prospective_summary: str | None,
    events_json: str | None,
    entity_id: str,
) -> str:
    """Compose the indexed_text value for the FTS5 table."""
    parts: list[str] = [claim]

    if prospective_summary:
        parts.append(prospective_summary)

    if events_json:
        try:
            events = json.loads(events_json)
            if isinstance(events, list):
                for ev in events:
                    if isinstance(ev, dict):
                        for key in ("event", "consequence", "temporal"):
                            val = ev.get(key)
                            if val:
                                parts.append(str(val))
        except (json.JSONDecodeError, TypeError):
            pass

    parts.append(entity_id)
    return "\n".join(parts)


def migrate(conn: sqlite3.Connection) -> None:
    # Migration 015 created content-sync triggers for the old FTS schema
    # (`claim`, `entity_id`). The v3 FTS table is manually maintained via
    # reindex_assertion_fts(), so those triggers must be removed before the
    # table shape changes or every new insert will crash at write time.
    conn.execute("DROP TRIGGER IF EXISTS assertions_fts_insert")
    conn.execute("DROP TRIGGER IF EXISTS assertions_fts_supersede")
    conn.execute("DROP TABLE IF EXISTS assertions_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE assertions_fts USING fts5("
        "  indexed_text,"
        "  assertion_id UNINDEXED,"
        "  entity_id UNINDEXED,"
        "  tokenize='porter unicode61'"
        ")"
    )
    logger.info("Created assertions_fts FTS5 virtual table")

    rows = conn.execute(
        "SELECT id, claim, prospective_summary, events_json, entity_id FROM assertions"
    ).fetchall()

    inserted = 0
    for row in rows:
        a_id, claim, prospective, events, eid = row
        indexed = _build_indexed_text(claim, prospective, events, eid)
        conn.execute(
            "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) "
            "VALUES (?, ?, ?)",
            (a_id, eid, indexed),
        )
        inserted += 1

    conn.commit()
    logger.info(
        "Migration 020 (assertion FTS5) complete — backfilled %d assertions",
        inserted,
    )
