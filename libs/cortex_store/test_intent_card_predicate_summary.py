"""Required case 1 — predicate_summary is never None.

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping. See
``_intent_card_test_fixtures`` for shared schema + insertion helpers.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import get_entity_card


def test_predicate_summary_never_none_on_empty_entity(
    migrated_conn: sqlite3.Connection,
) -> None:
    """Bare entity (no assertions, no edges) — slot is "" (empty string), not None."""
    conn = migrated_conn
    insert_entity(conn, entity_id="todo:bare", entity_type="todo")
    card = get_entity_card(conn, entity_id="todo:bare")
    assert card["predicate_summary"] is not None
    assert isinstance(card["predicate_summary"], str)


def test_predicate_summary_never_none_with_relationships(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    insert_entity(conn, entity_id="todo:linked", entity_type="todo")
    insert_entity(conn, entity_id="person:alice", entity_type="person")
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES "
        "('mentions', 'mentions')"
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, created_at) "
        "VALUES (?, ?, 'mentions', 1, ?)",
        ("todo:linked", "person:alice", datetime.now(UTC).isoformat()),
    )
    conn.commit()

    card = get_entity_card(conn, entity_id="todo:linked")
    assert card["predicate_summary"] is not None
    assert "mentions" in card["predicate_summary"]
