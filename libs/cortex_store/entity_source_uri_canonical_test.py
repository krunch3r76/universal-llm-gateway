"""Tests for canonical entity source_uri write boundary."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cortex_store.entity_crud import create_entity_impl, update_entity_impl


def _row(migrated_conn: sqlite3.Connection, entity_id: str) -> dict[str, object]:
    row = migrated_conn.execute(
        "SELECT source_uri, attributes, content_hash FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    assert row is not None
    attrs = row["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return {
        "source_uri": row["source_uri"],
        "attributes": attrs,
        "content_hash": row["content_hash"],
    }


def test_create_nested_only_promotes_and_strips(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "document:nested-only",
            "type": "document",
            "name": "Nested only",
            "attributes": {"source_uri": "agent-bus:4917#223", "domain": "cortex"},
        },
    )
    row = _row(migrated_conn, "document:nested-only")
    assert row["source_uri"] == "agent-bus:4917#223"
    assert row["attributes"] == {"domain": "cortex"}


def test_create_equal_dual_values_accept_and_strip(
    migrated_conn: sqlite3.Connection,
) -> None:
    uri = "cortex://notes/system/specs/foo.md"
    create_entity_impl(
        migrated_conn,
        {
            "id": "document:dual-equal",
            "type": "document",
            "name": "Dual equal",
            "source_uri": uri,
            "attributes": {"source_uri": uri},
        },
    )
    row = _row(migrated_conn, "document:dual-equal")
    assert row["source_uri"] == uri
    assert "source_uri" not in (row["attributes"] or {})


def test_create_differing_dual_values_reject_422(
    migrated_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(
            migrated_conn,
            {
                "id": "document:dual-diff",
                "type": "document",
                "name": "Dual diff",
                "source_uri": "cortex://a",
                "attributes": {"source_uri": "cortex://b"},
            },
        )
    assert exc.value.status_code == 422
    assert (
        migrated_conn.execute(
            "SELECT 1 FROM entities WHERE id = 'document:dual-diff'"
        ).fetchone()
        is None
    )


def test_create_explicit_null_canonical_with_nested_rejects_422(
    migrated_conn: sqlite3.Connection,
) -> None:
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(
            migrated_conn,
            {
                "id": "document:null-nested",
                "type": "document",
                "name": "Null nested",
                "source_uri": None,
                "attributes": {"source_uri": "agent-bus:1#1"},
            },
        )
    assert exc.value.status_code == 422


def test_create_empty_nested_strips_without_promotion(
    migrated_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        migrated_conn,
        {
            "id": "document:empty-nested",
            "type": "document",
            "name": "Empty nested",
            "attributes": {"source_uri": "", "tag": "x"},
        },
    )
    row = _row(migrated_conn, "document:empty-nested")
    assert row["source_uri"] is None
    assert row["attributes"] == {"tag": "x"}


def _seed_stranded(
    migrated_conn: sqlite3.Connection, entity_id: str = "todo:stranded"
) -> None:
    migrated_conn.execute(
        "INSERT INTO entities (id, type, name, attributes, created_at) "
        "VALUES (?, 'todo', 'Stranded', ?, '2026-07-15T00:00:00Z')",
        (
            entity_id,
            json.dumps(
                {
                    "source_uri": "agent-bus:4917#223",
                    "priority": "P1",
                    "domain": "cortex",
                }
            ),
        ),
    )
    migrated_conn.commit()


def test_partial_update_self_heals_stranded_row(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_stranded(migrated_conn)
    update_entity_impl(
        migrated_conn,
        entity_id="todo:stranded",
        updates={"attributes": {"note": "converged"}},
    )
    row = _row(migrated_conn, "todo:stranded")
    assert row["source_uri"] == "agent-bus:4917#223"
    assert row["attributes"] == {
        "priority": "P1",
        "domain": "cortex",
        "note": "converged",
    }


def test_empty_attributes_mapping_converges_inherited_nested(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_stranded(migrated_conn, "todo:empty-merge")
    update_entity_impl(
        migrated_conn,
        entity_id="todo:empty-merge",
        updates={"attributes": {}},
    )
    row = _row(migrated_conn, "todo:empty-merge")
    assert row["source_uri"] == "agent-bus:4917#223"
    assert "source_uri" not in (row["attributes"] or {})


def test_update_differing_dual_values_reject_before_mutation(
    migrated_conn: sqlite3.Connection,
) -> None:
    migrated_conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, attributes, created_at) "
        "VALUES ('document:live', 'document', 'Live', 'cortex://a', ?, '2026-07-15T00:00:00Z')",
        (json.dumps({"source_uri": "cortex://b"}),),
    )
    migrated_conn.commit()
    with pytest.raises(HTTPException) as exc:
        update_entity_impl(
            migrated_conn,
            entity_id="document:live",
            updates={"attributes": {"tag": "touch"}},
        )
    assert exc.value.status_code == 422
    row = _row(migrated_conn, "document:live")
    assert row["source_uri"] == "cortex://a"
    assert row["attributes"]["source_uri"] == "cortex://b"


def test_update_explicit_null_canonical_with_nested_rejects_422(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_stranded(migrated_conn, "todo:null-conflict")
    with pytest.raises(HTTPException) as exc:
        update_entity_impl(
            migrated_conn,
            entity_id="todo:null-conflict",
            updates={"source_uri": None},
        )
    assert exc.value.status_code == 422


def test_update_explicit_null_attributes_never_promotes(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_stranded(migrated_conn, "todo:attr-null")
    update_entity_impl(
        migrated_conn,
        entity_id="todo:attr-null",
        updates={"attributes": None},
    )
    row = _row(migrated_conn, "todo:attr-null")
    assert row["source_uri"] is None
    assert row["attributes"] is None


def test_rest_create_preserves_omitted_source_uri(
    cortex_client: TestClient,
) -> None:
    resp = cortex_client.post(
        "/entities",
        json={
            "id": "document:rest-omit",
            "type": "document",
            "name": "REST omit",
            "attributes": {"source_uri": "agent-bus:99#1"},
        },
    )
    assert resp.status_code == 201, resp.text
    row = cortex_client.get("/entities/document:rest-omit?intent=full").json()
    assert row["source_uri"] == "agent-bus:99#1"
    assert "source_uri" not in (row.get("attributes") or {})


def test_bulk_if_exists_update_does_not_skip_stranded(
    migrated_conn: sqlite3.Connection,
) -> None:
    from cortex_store.db import decode_row, query
    from cortex_store.dispatch_ops.ops_bulk_entities import _bulk_upsert_entity
    from cortex_store.entity_crud import ENTITY_JSON_FIELDS

    _seed_stranded(migrated_conn, "todo:bulk-skip")

    existing_rows = query(
        migrated_conn, "SELECT * FROM entities WHERE id = ?", ("todo:bulk-skip",)
    )
    existing = decode_row(existing_rows[0], ENTITY_JSON_FIELDS)
    assert existing.get("source_uri") in (None, "")

    result = _bulk_upsert_entity(
        migrated_conn,
        {
            "id": "todo:bulk-skip",
            "type": "todo",
            "name": "Bulk skip",
            "attributes": {"priority": "P1"},
        },
        if_exists="update",
    )
    assert result["action"] == "updated"
    row = _row(migrated_conn, "todo:bulk-skip")
    assert row["source_uri"] == "agent-bus:4917#223"
    assert "source_uri" not in (row["attributes"] or {})


def test_opaque_agent_bus_uri_byte_preserved(migrated_conn: sqlite3.Connection) -> None:
    uri = "agent-bus:4917#223"
    create_entity_impl(
        migrated_conn,
        {
            "id": "document:opaque",
            "type": "document",
            "name": "Opaque",
            "attributes": {"source_uri": uri},
        },
    )
    row = _row(migrated_conn, "document:opaque")
    assert row["source_uri"] == uri


def test_content_hash_computed_after_promotion_when_local_file_exists(
    migrated_conn: sqlite3.Connection,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _shared as dispatch_shared

    files_root = tmp_path / "files"
    rel = "notes/system/specs/promoted.md"
    target = files_root / rel
    target.parent.mkdir(parents=True)
    target.write_text("promoted body\n", encoding="utf-8")
    monkeypatch.setattr(dispatch_shared, "_FILES_ROOT", files_root)

    uri = f"cortex://{rel}"
    create_entity_impl(
        migrated_conn,
        {
            "id": "document:hash",
            "type": "document",
            "name": "Hash",
            "attributes": {"source_uri": uri},
        },
    )
    row = _row(migrated_conn, "document:hash")
    assert row["source_uri"] == uri
    assert row["content_hash"] is not None
    assert str(row["content_hash"]).startswith("sha256:")
