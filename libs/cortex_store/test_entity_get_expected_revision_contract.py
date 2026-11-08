"""entity_get expected_revision / expected_revisions contract (sparse gate)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
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


def _production_entities_has_no_revision() -> bool:
    cortex_pkg = Path(__file__).resolve().parent
    snap = cortex_pkg / "fixtures" / "live_schema_snapshot.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    sql = data["tables"]["entities"]["sql"]
    return "revision" not in sql.lower()


@pytest.mark.offline
def test_production_schema_snapshot_has_no_entities_revision_column() -> None:
    """Introspection tripwire — matches libs/cortex_store/fixtures/live_schema_snapshot.json."""
    assert _production_entities_has_no_revision()


@pytest.mark.offline
def test_expected_revision_unverifiable_without_column(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:rev-unverifiable"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {"entity_id": entity_id, "expected_revision": 0},
        )
    assert out["outcome"] == "revision_unverifiable"
    assert out["entity_id"] == entity_id
    assert out["revision"] is None


@pytest.mark.offline
def test_batch_expected_revisions_unverifiable_without_column(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:batch-rev-unverifiable"
    insert_entity(migrated_conn, entity_id=eid, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {
                "entity_ids": [eid],
                "intent": "preview",
                "expected_revisions": {eid: 0},
            },
        )
    assert "items" in out
    item = out["items"][0]
    assert item["outcome"] == "revision_unverifiable"
    assert item["revision"] is None
    assert "short_summary" not in item
    assert "selection" not in item
    other = "todo:batch-rev-other"
    insert_entity(migrated_conn, entity_id=other, entity_type="todo")
    missing = "todo:batch-rev-missing"
    with _patched_conn(migrated_conn):
        mixed = execute_op(
            "entity_get",
            {
                "entity_ids": [eid, other, missing],
                "expected_revisions": {eid: 0},
            },
        )
    assert [row["outcome"] for row in mixed["items"]] == [
        "revision_unverifiable",
        "ok",
        "missing",
    ]
    assert mixed["items"][0]["revision"] is None
    assert "short_summary" not in mixed["items"][0]
    assert mixed["items"][1]["outcome"] == "ok"


@pytest.mark.offline
def test_expected_revision_scalar_on_batch_rejected(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:scalar-batch"
    insert_entity(migrated_conn, entity_id=eid, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {
                "entity_ids": [eid],
                "intent": "preview",
                "expected_revision": 1,
            },
        )
    assert out.get("status_code") == 400
    assert out["outcome"] == "expected_revision_scalar_on_batch"


@pytest.mark.offline
def test_expected_revisions_not_applicable_on_single(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:single-map"
    insert_entity(migrated_conn, entity_id=eid, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {"entity_id": eid, "expected_revisions": {eid: 1}},
        )
    assert out.get("status_code") == 400
    assert out["outcome"] == "param_not_applicable"
    assert out["data"]["parameter"] == "expected_revisions"


@pytest.mark.offline
def test_expected_revision_conflict_scalar_and_map(
    migrated_conn: sqlite3.Connection,
) -> None:
    eid = "todo:conflict-rev"
    insert_entity(migrated_conn, entity_id=eid, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {
                "entity_id": eid,
                "expected_revision": 1,
                "expected_revisions": {eid: 1},
            },
        )
    assert out.get("status_code") == 400
    assert out["outcome"] == "expected_revision_conflict"


@pytest.mark.offline
def test_expected_revisions_unmatched_key(
    migrated_conn: sqlite3.Connection,
) -> None:
    a = "todo:batch-a"
    b = "todo:batch-b"
    insert_entity(migrated_conn, entity_id=a, entity_type="todo")
    insert_entity(migrated_conn, entity_id=b, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {
                "entity_ids": [a, b],
                "intent": "preview",
                "expected_revisions": {a: 0, b: 0, "todo:stranger": 0},
            },
        )
    assert out.get("status_code") == 400
    assert out["outcome"] == "expected_revisions_unmatched"
    assert "todo:stranger" in out["data"]["unmatched_keys"]


@pytest.mark.offline
def test_unknown_id_with_expected_revision_is_missing(
    migrated_conn: sqlite3.Connection,
) -> None:
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {
                "entity_id": "todo:does-not-exist",
                "expected_revision": 1,
                "intent": "card",
            },
        )
    assert out["outcome"] == "missing"
    assert "top_k_assertions" not in out


@pytest.mark.offline
def test_expected_revision_rejected_for_full_intent(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:full-precondition"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="todo")
    with _patched_conn(migrated_conn):
        out = execute_op(
            "entity_get",
            {"entity_id": entity_id, "intent": "full", "expected_revision": 1},
        )
    assert out["outcome"] == "param_not_applicable"
    assert "assertions" not in out
    assert out.get("data", {}).get("parameter") == "expected_revision"
