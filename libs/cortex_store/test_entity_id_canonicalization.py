"""Regression tests for compound entity id canonicalization at create intake."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.dispatch_ops.ops_bulk_entities import _op_entities_bulk_upsert
from cortex_store.entity_crud import create_entity_impl
from cortex_store.entity_read import get_entity_impl


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def test_bare_slug_canonicalized_to_compound_id(conn: sqlite3.Connection) -> None:
    result = create_entity_impl(
        conn,
        {"id": "test-proj", "type": "project", "name": "Test Project"},
    )
    assert result["id"] == "project:test-proj"
    fetched = get_entity_impl(conn, entity_id="project:test-proj")
    assert fetched["id"] == "project:test-proj"


def test_compound_id_unchanged_no_double_prefix(conn: sqlite3.Connection) -> None:
    result = create_entity_impl(
        conn,
        {"id": "project:test-proj2", "type": "project", "name": "Test Project 2"},
    )
    assert result["id"] == "project:test-proj2"


def test_mismatched_type_prefix_rejected_422(conn: sqlite3.Connection) -> None:
    with pytest.raises(HTTPException) as exc_info:
        create_entity_impl(
            conn,
            {"id": "decision:x", "type": "project", "name": "Bad"},
        )
    assert exc_info.value.status_code == 422


def test_bulk_upsert_canonicalizes_and_skips_repeat(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_bulk_entities.cortex_conn", lambda: conn
    )
    payload = {
        "entities": [
            {
                "id": "bulk-proj",
                "type": "project",
                "name": "Bulk Project",
            }
        ],
        "if_exists": "skip",
    }
    first = _op_entities_bulk_upsert(**payload)
    assert first["items"][0]["id"] == "project:bulk-proj"
    assert first["items"][0]["action"] == "created"
    row = conn.execute(
        "SELECT id FROM entities WHERE id = ?", ("project:bulk-proj",)
    ).fetchone()
    assert row is not None

    second = _op_entities_bulk_upsert(**payload)
    assert second["items"][0]["action"] == "skipped"
