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


@pytest.mark.offline
def test_preview_includes_content_hash(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:sparse-preview-hash"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    migrated_conn.execute(
        "UPDATE entities SET content_hash = ? WHERE id = ?",
        ("abc123hash", entity_id),
    )
    migrated_conn.commit()
    with _patched_conn(migrated_conn):
        result = execute_op("entity_get", {"entity_id": entity_id})
    assert result.get("content_hash") == "abc123hash"


@pytest.mark.offline
def test_batch_stale_handle_per_entity_revision(migrated_conn: sqlite3.Connection) -> None:
    """Labeled non-production seam.

    ``live_schema_snapshot.json`` entities has no revision column. This ALTER
    is not production proof; a mismatch is stale_handle only when that column
    was added by the test.
    """
    migrated_conn.execute("ALTER TABLE entities ADD COLUMN revision INTEGER DEFAULT 0")
    migrated_conn.commit()
    eid_a = "todo:stale-a"
    eid_b = "todo:stale-b"
    insert_entity(migrated_conn, entity_id=eid_a, entity_type="todo")
    insert_entity(migrated_conn, entity_id=eid_b, entity_type="todo")
    migrated_conn.execute("UPDATE entities SET revision = 2 WHERE id = ?", (eid_a,))
    migrated_conn.execute("UPDATE entities SET revision = 5 WHERE id = ?", (eid_b,))
    migrated_conn.commit()
    with _patched_conn(migrated_conn):
        result = execute_op(
            "entity_get",
            {
                "entity_ids": [eid_a, eid_b],
                "intent": "preview",
                "expected_revisions": {eid_a: 1, eid_b: 5},
            },
        )
    assert result["items"][0]["ok"] is False
    assert result["items"][0]["outcome"] == "stale_handle"
    assert result["items"][0]["revision"] == 2
    assert result["items"][1]["ok"] is True
    assert result["items"][1]["revision"] == 5


def _edge(conn: sqlite3.Connection, left: int, right: int, *, retired: bool = False) -> None:
    conn.execute(
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type, valid_until) "
        "VALUES (?, ?, ?, ?, 'contradicts', ?)",
        (
            "preview-test",
            "tester",
            f"assertion:{left}",
            f"assertion:{right}",
            "2020-01-01T00:00:00" if retired else None,
        ),
    )
    conn.commit()


@pytest.mark.offline
def test_preview_conflict_uses_live_contradicts_edge(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:preview-conflict"
    insert_entity(
        migrated_conn,
        entity_id=entity_id,
        entity_type="todo",
        description="Stored description that does not mention the buried conflict.",
    )
    older = insert_assertion(migrated_conn, entity_id=entity_id, claim="buried conflict claim")
    newer = insert_assertion(migrated_conn, entity_id=entity_id, claim="freshest claim")
    migrated_conn.execute(
        "UPDATE assertions SET observed_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00", older),
    )
    migrated_conn.execute(
        "UPDATE assertions SET observed_at = ? WHERE id = ?",
        ("2024-06-01T00:00:00", newer),
    )
    migrated_conn.commit()
    _edge(migrated_conn, older, newer)
    with _patched_conn(migrated_conn):
        preview = execute_op("entity_get", {"entity_id": entity_id})
    assert preview["contradiction_count"] == 1
    assert preview["conflict_assertion_id"] == older
    assert preview["freshest_assertion_id"] == newer
    assert preview["confidence"]
    assert preview["review_status"]
    assert preview["summary_as_of"]
    assert preview["summary_source"]["field"] == "description"
    assert preview["summary_source"]["revision"] is None
    assert "buried conflict claim" not in preview["short_summary"]


@pytest.mark.offline
def test_preview_without_contradicts_edge_has_zero_count(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:preview-no-conflict"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    insert_assertion(migrated_conn, entity_id=entity_id, claim="only claim")
    with _patched_conn(migrated_conn):
        preview = execute_op("entity_get", {"entity_id": entity_id})
    assert preview["contradiction_count"] == 0
    assert preview["conflict_assertion_id"] is None


@pytest.mark.offline
def test_superseded_contradiction_does_not_count(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:preview-retired-conflict"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    older = insert_assertion(migrated_conn, entity_id=entity_id, claim="old")
    newer = insert_assertion(migrated_conn, entity_id=entity_id, claim="new")
    migrated_conn.execute(
        "UPDATE assertions SET superseded_by = ? WHERE id = ?",
        (newer, older),
    )
    migrated_conn.commit()
    _edge(migrated_conn, older, newer)
    with _patched_conn(migrated_conn):
        preview = execute_op("entity_get", {"entity_id": entity_id})
    assert preview["contradiction_count"] == 0


@pytest.mark.offline
def test_preview_revision_when_column_present(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.execute("ALTER TABLE entities ADD COLUMN revision INTEGER DEFAULT 0")
    migrated_conn.commit()
    entity_id = "todo:sparse-preview-rev"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    migrated_conn.execute(
        "UPDATE entities SET revision = 7 WHERE id = ?",
        (entity_id,),
    )
    migrated_conn.commit()
    with _patched_conn(migrated_conn):
        result = execute_op("entity_get", {"entity_id": entity_id})
    assert result.get("revision") == 7
