"""Entity card exposes the entities.source_uri column."""

from __future__ import annotations

import sqlite3

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import get_entity_card


def test_card_includes_source_uri(migrated_conn: sqlite3.Connection) -> None:
    uri = "workspaces://universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/x/SKILL.md"
    insert_entity(migrated_conn, entity_id="agent_skill:x", entity_type="agent_skill")
    migrated_conn.execute(
        "UPDATE entities SET source_uri = ? WHERE id = ?",
        (uri, "agent_skill:x"),
    )
    migrated_conn.commit()
    card = get_entity_card(migrated_conn, entity_id="agent_skill:x")
    assert card["source_uri"] == uri
