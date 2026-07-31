"""End-to-end mention_type filtering for surface_forms dispatch op."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
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

    with patch("cortex_store.routes.surface_forms.cortex_conn", _Ctx):
        yield


def _insert_surface_form(
    conn: sqlite3.Connection,
    *,
    mention: str,
    entity_id: str,
    mention_type: str,
    context_hash: str,
) -> None:
    conn.execute(
        "INSERT INTO surface_forms (mention, entity_id, context_hash, mention_type) "
        "VALUES (?, ?, ?, ?)",
        (mention, entity_id, context_hash, mention_type),
    )
    conn.commit()


@pytest.mark.offline
def test_surface_forms_filters_by_mention_type(
    migrated_conn: sqlite3.Connection,
) -> None:
    conn = migrated_conn
    entity_id = "decision:surface-form-filter"
    insert_entity(conn, entity_id=entity_id, entity_type="decision")
    _insert_surface_form(
        conn,
        mention="Alpha",
        entity_id=entity_id,
        mention_type="name",
        context_hash="hash-name",
    )
    _insert_surface_form(
        conn,
        mention="Beta",
        entity_id=entity_id,
        mention_type="alias",
        context_hash="hash-alias",
    )

    with _patched_conn(conn):
        name_only = execute_op(
            "surface_forms",
            {"entity_id": entity_id, "mention_type": "name"},
        )
        alias_only = execute_op(
            "surface_forms",
            {"entity_id": entity_id, "mention_type": "alias"},
        )

    assert "error" not in name_only
    assert "error" not in alias_only
    assert len(name_only["items"]) == 1
    assert len(alias_only["items"]) == 1
    assert name_only["items"][0]["mention"] == "Alpha"
    assert name_only["items"][0]["mention_type"] == "name"
    assert alias_only["items"][0]["mention"] == "Beta"
    assert alias_only["items"][0]["mention_type"] == "alias"
