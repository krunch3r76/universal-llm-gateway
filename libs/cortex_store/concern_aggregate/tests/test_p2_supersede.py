"""Supersede 409 additive concern fields (B10)."""

from __future__ import annotations

import pytest
from cortex_store.concern_aggregate.tests.conftest import append_position_head
from cortex_store.routes.assertions._supersede import concern_head_conflict_fields

pytestmark = pytest.mark.offline


def test_b10_supersede_409_includes_head_fields(concern_db) -> None:
    entity_id = "concern:sup-b10"
    concern_db.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "concern", "sup"),
    )
    concern_db.commit()
    append_position_head(
        concern_db,
        entity_id,
        record_ref="rec-sup-head",
        write_intent_id="wi:sup",
    )
    fields = concern_head_conflict_fields(concern_db, entity_id)
    assert fields == {
        "current_revision": 1,
        "current_head_ref": "rec-sup-head",
    }
    assert concern_head_conflict_fields(concern_db, "concern:missing") == {}
