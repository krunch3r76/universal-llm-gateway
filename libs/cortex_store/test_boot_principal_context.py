"""Tests for GET /boot-principal-context."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.routes.boot.principal_context import get_boot_principal_context

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT,
    name TEXT,
    attributes TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT,
    claim TEXT,
    confidence TEXT,
    review_status TEXT,
    superseded_by INTEGER,
    resolution_status TEXT,
    valid_from TEXT,
    valid_until TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def test_get_boot_principal_context_projects_durable_identity_only() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'person', ?, ?)",
        (
            "person:test-principal",
            "Test Person",
            json.dumps({"durable_identity": "Stable identity line."}),
        ),
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, review_status, valid_until) "
        "VALUES ('person:test-principal', 'SENSITIVE person stream', 'confirmed', "
        "'committed', NULL)"
    )
    conn.commit()

    with patch(
        "cortex_store.routes.boot.principal_context.cortex_conn", return_value=conn
    ):
        body = get_boot_principal_context(
            principal="person:test-principal", active_limit=5
        )

    assert body["durable_identity"] == "Stable identity line."
    assert body["active_matters"] == []
    assert "SENSITIVE" not in json.dumps(body)


def test_get_boot_principal_context_legal_matter_allowlist_only() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'person', ?, NULL)",
        ("person:test-principal", "Test Person"),
    )
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('legal_matter:test', 'legal_matter', 'Matter')"
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, review_status, valid_until) "
        "VALUES ('legal_matter:test', 'Appeal window open.', 'confirmed', 'committed', "
        "datetime('now', '+30 days'))"
    )
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, review_status, valid_until) "
        "VALUES ('person:test-principal', 'Must not surface', 'confirmed', 'committed', "
        "datetime('now', '+30 days'))"
    )
    conn.commit()

    with patch(
        "cortex_store.routes.boot.principal_context.cortex_conn", return_value=conn
    ):
        body = get_boot_principal_context(
            principal="person:test-principal", active_limit=5
        )

    assert body["durable_identity"] is None
    assert len(body["active_matters"]) == 1
    assert body["active_matters"][0]["entity_id"] == "legal_matter:test"
    assert "Must not surface" not in json.dumps(body)


def test_get_boot_principal_context_rejects_non_person() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('legal_matter:test', 'legal_matter', 'M')"
    )
    conn.commit()

    with patch(
        "cortex_store.routes.boot.principal_context.cortex_conn", return_value=conn
    ):
        with pytest.raises(HTTPException) as exc:
            get_boot_principal_context(principal="legal_matter:test", active_limit=5)

    assert exc.value.status_code == 422
