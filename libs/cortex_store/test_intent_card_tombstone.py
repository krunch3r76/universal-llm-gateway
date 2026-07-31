"""Required case 2 — Tombstone-only entity returns [summary] not [pointers].

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping.
"""

from __future__ import annotations

import sqlite3

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.card import get_entity_card


def test_tombstone_collapses_to_summary_in_top_k(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="todo:tombstoned", entity_type="todo")
    # The summary lives in a superseded row (it was the supersede-input
    # before pointers replaced it).
    summary_id = insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim="archive summary 9999 — consolidated state of this todo",
        superseded_by=None,
    )
    # Active rows are pure pointers — entity is tombstone-only.
    pointer_id = insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )
    insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    # Mark the summary row superseded (FK-safe: point at a real assertion id).
    conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (pointer_id, summary_id),
    )
    conn.commit()

    # Now active rows are only pointers; rebuild card.
    insert_assertion(
        conn,
        entity_id="todo:tombstoned",
        claim=f"Compacted into archive summary {summary_id}",
    )

    card = get_entity_card(conn, entity_id="todo:tombstoned")
    top_k = card["top_k_assertions"]
    assert len(top_k) == 1, "Tombstone-collapse must yield exactly the summary row"
    assert top_k[0]["claim"].startswith("archive summary"), (
        f"Expected summary claim, got: {top_k[0]['claim']!r}"
    )
    assert "Compacted into" not in top_k[0]["claim"]
    assert card["predicate_summary"] is not None
