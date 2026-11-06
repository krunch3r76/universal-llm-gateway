"""entity_get preview — sparse default for omitted intent."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops import execute_op


@contextmanager
def _patched_conn(conn: sqlite3.Connection):
    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *a: object) -> None:
            return None

    with patch("cortex_store.dispatch_ops.ops_entities.cortex_conn", _Ctx):
        yield


@pytest.mark.offline
def test_omitted_intent_returns_preview(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:sparse-preview-default"
    insert_entity(
        migrated_conn,
        entity_id=entity_id,
        entity_type="todo",
        description="Stored description for preview clip.",
    )
    insert_assertion(migrated_conn, entity_id=entity_id, claim="Active claim.")

    with _patched_conn(migrated_conn):
        preview = execute_op("entity_get", {"entity_id": entity_id})
        card = execute_op("entity_get", {"entity_id": entity_id, "intent": "card"})

    assert preview.get("selection") == "preview"
    assert "attributes" not in preview
    assert "top_k_assertions" not in preview
    assert card.get("intent") == "card"
    assert "top_k_assertions" in card


@pytest.mark.offline
def test_explicit_preview_intent(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:sparse-preview-explicit"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get", {"entity_id": entity_id, "intent": "preview"}
        )
    assert result.get("intent") == "preview"
