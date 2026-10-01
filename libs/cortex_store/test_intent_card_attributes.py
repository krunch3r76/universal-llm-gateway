"""Card v0 carries entity attributes when the column has an object.

The card fetch already selects ``entities.attributes``. Dropping that
object forced card readers onto ``intent=full`` (scoreboard rows, density
triage). An entity with no attributes must stay a small card.
"""

from __future__ import annotations

import json
import sqlite3

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.card import get_entity_card

_ROWS = {
    "rows": ["R1", "R2"],
    "acceptance_criteria": ["carry attributes on the card"],
    "derived_from": "document:frame",
}


def test_card_includes_attributes_when_present(migrated_conn: sqlite3.Connection) -> None:
    """A card built from an entity that has attributes includes those attributes."""
    insert_entity(
        migrated_conn,
        entity_id="todo:with-attrs",
        entity_type="todo",
        attributes=json.dumps(_ROWS),
    )
    card = get_entity_card(migrated_conn, entity_id="todo:with-attrs")
    assert card["attributes"] == _ROWS


def test_card_without_attributes_stays_small(migrated_conn: sqlite3.Connection) -> None:
    """A card for an entity with no attributes does not grow by an attribute blob."""
    insert_entity(
        migrated_conn,
        entity_id="todo:no-attrs",
        entity_type="todo",
        attributes=None,
    )
    card = get_entity_card(migrated_conn, entity_id="todo:no-attrs")
    assert card.get("attributes") in (None, {})
    without = {key: value for key, value in card.items() if key != "attributes"}
    overhead = len(json.dumps(card)) - len(json.dumps(without))
    assert overhead <= 32
