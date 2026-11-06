"""Concern-typed card coherence (B5)."""

from __future__ import annotations

import sqlite3

import pytest
from cortex_store.card import get_entity_card
from cortex_store.concern_aggregate.tests.conftest import append_position_head

pytestmark = pytest.mark.offline


def test_b5_card_serves_concern_head(concern_db: sqlite3.Connection) -> None:
    entity_id = "concern:card-b5"
    concern_db.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "concern", "Card concern"),
    )
    concern_db.commit()
    append_position_head(
        concern_db,
        entity_id,
        record_ref="rec-card-head",
        write_intent_id="wi:card",
        material={"position": "open"},
    )
    concern_db.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type, "
        "predicate_form, review_status) VALUES (?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            "status(receipt:done)",
            "confirmed",
            "inference",
            "status(receipt:done)",
            "accepted",
        ),
    )
    concern_db.commit()
    card = get_entity_card(concern_db, entity_id=entity_id)
    cs = card["current_status"]
    assert cs["concern_head"] is not None
    assert cs["concern_head"]["record_ref"] == "rec-card-head"
    assert cs["concern_head"]["revision"] == 1
    assert cs["served"] is None
