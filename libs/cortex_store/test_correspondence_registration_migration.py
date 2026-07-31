"""Unit tests for migration 048 (correspondence workflow + confidence field)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent
    / "migrations"
    / "048_correspondence_workflow_and_confidence.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_048_correspondence_workflow_and_confidence", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_048 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_048)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE workflow_schemas (
            entity_type TEXT PRIMARY KEY,
            enum_values TEXT NOT NULL,
            initial_state TEXT NOT NULL,
            terminal_states TEXT,
            notes TEXT
        );
        CREATE TABLE type_confidence_fields (
            entity_type TEXT PRIMARY KEY,
            confidence_field TEXT NOT NULL
        );
        """
    )
    return c


def test_correspondence_workflow_seeded(conn: sqlite3.Connection) -> None:
    migration_048.migrate(conn)
    row = conn.execute(
        "SELECT enum_values, initial_state, terminal_states "
        "FROM workflow_schemas WHERE entity_type = 'correspondence'"
    ).fetchone()
    assert row is not None
    assert json.loads(row["enum_values"]) == [
        "pending_review",
        "processed",
        "dismissed",
    ]
    assert row["initial_state"] == "pending_review"
    assert json.loads(row["terminal_states"]) == ["processed", "dismissed"]


def test_correspondence_confidence_field_seeded(conn: sqlite3.Connection) -> None:
    migration_048.migrate(conn)
    row = conn.execute(
        "SELECT confidence_field FROM type_confidence_fields "
        "WHERE entity_type = 'correspondence'"
    ).fetchone()
    assert row is not None
    assert row["confidence_field"] == "content_hash"


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_048.migrate(conn)
    migration_048.migrate(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM workflow_schemas WHERE entity_type = 'correspondence'"
    ).fetchone()["n"]
    assert count == 1


def test_correspondence_create_stamps_pending_review(
    migrated_conn: sqlite3.Connection,
) -> None:
    """When workflow_schema exists, omitted workflow_state → initial_state."""
    from cortex_store.entity_crud import create_entity_impl

    result = create_entity_impl(
        migrated_conn,
        {
            "id": "correspondence:test-1169",
            "type": "correspondence",
            "name": "Test mail",
            "attributes": {"profile": "probate"},
        },
    )
    assert result["workflow_state"] == "pending_review"
    assert result.get("confidence_band") == "unsubstantiated"
