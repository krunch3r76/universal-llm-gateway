"""Tests for matter genus registry (type_taxonomy + migration 064 + read/retype guards)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops.ops_entities import _op_entities, _op_entity_retype
from cortex_store.entity_crud import (
    create_entity_impl,
    list_entities_impl,
    update_entity_impl,
)
from cortex_store.entity_rekey_core import entity_retype_impl
from cortex_store.type_schemas import validate_required_attributes
from cortex_store.type_taxonomy import (
    MATTER_MODES,
    MATTER_SPECIES,
    category_species,
)

_MIG_PATH = Path(__file__).parent / "migrations" / "064_matter_genus_mode.py"
_spec = importlib.util.spec_from_file_location(
    "migration_064_matter_genus_mode", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_064 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_064)


@pytest.fixture()
def schema_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE type_attribute_schemas (
            entity_type TEXT PRIMARY KEY,
            required_keys TEXT NOT NULL,
            optional_keys TEXT NOT NULL,
            enum_constraints TEXT NOT NULL,
            notes TEXT
        );
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            workflow_state TEXT,
            aliases TEXT,
            attributes TEXT,
            content_hash TEXT,
            created_at TEXT DEFAULT '2026-01-01T00:00:00Z',
            updated_at TEXT,
            lifecycle TEXT DEFAULT 'active',
            confidence_band TEXT,
            adoption TEXT
        );
        """
    )
    return c


def test_category_species_matter_returns_four() -> None:
    species = category_species("matter")
    assert species == MATTER_SPECIES
    assert species is not None
    assert len(species) == 4


def test_category_species_unknown_returns_none() -> None:
    assert category_species("bogus") is None


def test_migration_registers_mode_enum_all_species(
    schema_conn: sqlite3.Connection,
) -> None:
    migration_064.migrate(schema_conn)
    for species in MATTER_SPECIES:
        row = schema_conn.execute(
            "SELECT optional_keys, enum_constraints FROM type_attribute_schemas "
            "WHERE entity_type = ?",
            (species,),
        ).fetchone()
        assert row is not None, f"missing schema for {species}"
        assert "mode" in json.loads(row["optional_keys"])
        enums = json.loads(row["enum_constraints"])
        assert set(enums["mode"]) == set(MATTER_MODES)


def test_migration_preserves_existing_case_row(schema_conn: sqlite3.Connection) -> None:
    schema_conn.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "case",
            json.dumps(["court"]),
            json.dumps(["docket"]),
            json.dumps({}),
            "Pre-existing case notes.",
        ),
    )
    migration_064.migrate(schema_conn)
    row = schema_conn.execute(
        "SELECT required_keys, optional_keys, notes "
        "FROM type_attribute_schemas WHERE entity_type = 'case'"
    ).fetchone()
    assert json.loads(row["required_keys"]) == ["court"]
    assert "docket" in json.loads(row["optional_keys"])
    assert "mode" in json.loads(row["optional_keys"])
    assert "Pre-existing case notes." in row["notes"]


def test_migration_idempotent_rerun(schema_conn: sqlite3.Connection) -> None:
    migration_064.migrate(schema_conn)
    first = schema_conn.execute(
        "SELECT optional_keys, enum_constraints, notes FROM type_attribute_schemas "
        "WHERE entity_type = 'work'"
    ).fetchone()
    migration_064.migrate(schema_conn)
    second = schema_conn.execute(
        "SELECT optional_keys, enum_constraints, notes FROM type_attribute_schemas "
        "WHERE entity_type = 'work'"
    ).fetchone()
    assert first == second


def test_mode_enum_validates_on_create_and_update(
    schema_conn: sqlite3.Connection,
) -> None:
    migration_064.migrate(schema_conn)
    validate_required_attributes(schema_conn, "finance", {"mode": "stewardship"})
    with pytest.raises(HTTPException) as exc:
        validate_required_attributes(schema_conn, "finance", {"mode": "dispute"})
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "type_attribute_enum_violation"


def test_mode_enum_non_matter_unaffected(schema_conn: sqlite3.Connection) -> None:
    migration_064.migrate(schema_conn)
    validate_required_attributes(schema_conn, "todo", {"mode": "dispute"})


def test_list_entities_category_matter_spans_species(
    schema_conn: sqlite3.Connection,
) -> None:
    for eid, etype in (
        ("work:alpha", "work"),
        ("finance:beta", "finance"),
        ("todo:gamma", "todo"),
    ):
        schema_conn.execute(
            "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
            (eid, etype, eid.split(":", 1)[-1]),
        )
    result = list_entities_impl(schema_conn, category="matter", limit=50)
    types = {item["type"] for item in result["items"]}
    assert "work" in types
    assert "finance" in types
    assert "todo" not in types


def test_list_entities_category_type_intersection_empty(
    schema_conn: sqlite3.Connection,
) -> None:
    schema_conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('work:x', 'work', 'x')"
    )
    result = list_entities_impl(
        schema_conn, category="matter", entity_type="todo", limit=50
    )
    assert result == {"items": []}


def test_list_entities_unknown_category_error(schema_conn: sqlite3.Connection) -> None:
    result = list_entities_impl(schema_conn, category="bogus")
    assert "error" in result
    assert "Unknown category" in result["error"]
    assert "matter" in result["error"]


def test_table_enum_matches_matter_modes(schema_conn: sqlite3.Connection) -> None:
    migration_064.migrate(schema_conn)
    for species in MATTER_SPECIES:
        row = schema_conn.execute(
            "SELECT enum_constraints FROM type_attribute_schemas WHERE entity_type = ?",
            (species,),
        ).fetchone()
        table_modes = set(json.loads(row["enum_constraints"])["mode"])
        assert table_modes == set(MATTER_MODES)


@pytest.fixture()
def dispatch_conn(
    migrated_conn: sqlite3.Connection,
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> sqlite3.Connection:
    bind_cortex_db(monkeypatch, migrated_db_path)
    return migrated_conn


def test_retype_finance_blocked_without_force(
    dispatch_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        dispatch_conn,
        {"id": "finance:block-test", "type": "finance", "name": "block-test"},
    )
    with pytest.raises(HTTPException) as exc:
        entity_retype_impl(dispatch_conn, "finance:block-test", "work")
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "matter_genus_retype_blocked"


def test_retype_finance_passes_with_force(dispatch_conn: sqlite3.Connection) -> None:
    create_entity_impl(
        dispatch_conn,
        {"id": "finance:force-me", "type": "finance", "name": "force-me"},
    )
    result = entity_retype_impl(
        dispatch_conn, "finance:force-me", "work", force=True
    )
    assert result["new_type"] == "work"
    assert result["new_id"] == "work:force-me"


def test_retype_into_matter_species_from_non_species(
    dispatch_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        dispatch_conn,
        {
            "id": "investigation:adopt",
            "type": "investigation",
            "name": "adopt",
        },
    )
    result = entity_retype_impl(dispatch_conn, "investigation:adopt", "case")
    assert result["new_type"] == "case"
    assert result["new_id"] == "case:adopt"


def test_op_entities_category_matter(dispatch_conn: sqlite3.Connection) -> None:
    create_entity_impl(
        dispatch_conn,
        {"id": "work:op-test", "type": "work", "name": "op-test"},
        commit=False,
    )
    create_entity_impl(
        dispatch_conn,
        {"id": "case:op-test", "type": "case", "name": "op-test"},
        commit=False,
    )
    create_entity_impl(
        dispatch_conn,
        {"id": "todo:op-test", "type": "todo", "name": "op-test"},
        commit=False,
    )
    dispatch_conn.commit()
    result = _op_entities(category="matter", limit=100)
    types = {item["type"] for item in result["items"]}
    assert "work" in types
    assert "case" in types


def test_entity_create_update_mode_via_impl(
    dispatch_conn: sqlite3.Connection,
) -> None:
    create_entity_impl(
        dispatch_conn,
        {
            "id": "work:mode-test",
            "type": "work",
            "name": "mode-test",
            "attributes": {"mode": "stewardship"},
        },
    )
    update_entity_impl(
        dispatch_conn,
        entity_id="work:mode-test",
        updates={"attributes": {"mode": "conflict"}},
    )
    with pytest.raises(HTTPException) as exc:
        create_entity_impl(
            dispatch_conn,
            {
                "id": "finance:bad-mode",
                "type": "finance",
                "name": "bad-mode",
                "attributes": {"mode": "dispute"},
            },
        )
    assert exc.value.detail["error"] == "type_attribute_enum_violation"


def test_op_entity_retype_force_plumbing(dispatch_conn: sqlite3.Connection) -> None:
    create_entity_impl(
        dispatch_conn,
        {"id": "finance:retype-op", "type": "finance", "name": "retype-op"},
    )
    blocked = _op_entity_retype(entity_id="finance:retype-op", new_type="work")
    assert blocked.get("status_code") == 422
    assert blocked["error"]["error"] == "matter_genus_retype_blocked"
    ok = _op_entity_retype(entity_id="finance:retype-op", new_type="work", force=True)
    assert ok["new_id"] == "work:retype-op"
