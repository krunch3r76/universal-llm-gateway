"""Unit tests for migration 037 (entity-backed claim provenance v1 schema).

Applies the migration to an in-memory SQLite DB pre-seeded with the
minimum prerequisite tables and confirms:

  * Four workflow_schemas rows are seeded
    (legal_source, exhibit, case-law, brief).
  * type_attribute_schemas table is created and seeded with the four
    type contracts from spec § 1.1 / § 1.2 / § 1.3 / § 4.1.
  * chunks.pinpoint column is added when chunks exists, and the lookup
    index ``idx_chunks_source_pinpoint`` is created.
  * Re-running the migration is a no-op (idempotent contract held by
    the canonical run_migrations runner).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = Path(__file__).parent / "migrations" / "037_provenance_v1_schema.py"
_spec = importlib.util.spec_from_file_location(
    "migration_037_provenance_v1_schema", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_037 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_037)


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
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            source_uri TEXT,
            source_date TEXT,
            observer TEXT,
            chunk_index INTEGER,
            token_count INTEGER
        );
        """
    )
    return c


def test_workflow_schemas_seeded(conn: sqlite3.Connection) -> None:
    migration_037.migrate(conn)
    rows = {
        row["entity_type"]: row
        for row in conn.execute(
            "SELECT entity_type, enum_values, initial_state, "
            "       terminal_states, notes "
            "FROM workflow_schemas"
        )
    }
    for expected_type in ("legal_source", "case-law", "exhibit", "brief"):
        assert expected_type in rows, f"{expected_type} missing"

    assert json.loads(rows["legal_source"]["enum_values"]) == [
        "active",
        "superseded",
    ]
    assert rows["brief"]["initial_state"] == "drafting"
    assert json.loads(rows["exhibit"]["terminal_states"]) == ["withdrawn"]


def test_type_attribute_schemas_seeded(conn: sqlite3.Connection) -> None:
    migration_037.migrate(conn)
    rows = {
        row["entity_type"]: row
        for row in conn.execute(
            "SELECT entity_type, required_keys, optional_keys, "
            "       enum_constraints "
            "FROM type_attribute_schemas"
        )
    }

    legal = rows["legal_source"]
    assert "citation_canonical" in json.loads(legal["required_keys"])
    assert "authority_class" in json.loads(legal["required_keys"])
    enums = json.loads(legal["enum_constraints"])
    assert "probate_code" in enums["authority_class"]

    exhibit = rows["exhibit"]
    enums_exh = json.loads(exhibit["enum_constraints"])
    assert "decree" in enums_exh["document_kind"]
    assert "mailed_original" in enums_exh["authentication_basis"]

    brief = rows["brief"]
    # § 4.1 — brief carries no required attributes at create time
    assert json.loads(brief["required_keys"]) == []


def test_chunks_pinpoint_column_added(conn: sqlite3.Connection) -> None:
    migration_037.migrate(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()]
    assert "pinpoint" in cols

    indexes = [row[1] for row in conn.execute("PRAGMA index_list(chunks)").fetchall()]
    assert "idx_chunks_source_pinpoint" in indexes


def test_migration_is_idempotent(conn: sqlite3.Connection) -> None:
    migration_037.migrate(conn)
    migration_037.migrate(conn)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM workflow_schemas WHERE entity_type = ?",
        ("legal_source",),
    ).fetchone()["n"]
    assert n == 1


def test_pinpoint_column_skipped_when_chunks_table_absent() -> None:
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
        """
    )
    migration_037.migrate(c)
    assert (
        c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        is None
    )
    # type_attribute_schemas still seeded
    assert c.execute("SELECT COUNT(*) FROM type_attribute_schemas").fetchone()[0] == 4
