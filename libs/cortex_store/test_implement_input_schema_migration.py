"""Unit tests for migration 059 (implement-input schema registry)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "059_implement_input_schema_registry.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_059_implement_input_schema_registry", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_059 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_059)


@pytest.fixture()
def conn() -> sqlite3.Connection:
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
            attributes TEXT
        );
        """
    )
    return c


def test_registers_todo_plan_plan_phase_schemas(conn: sqlite3.Connection) -> None:
    migration_059.migrate(conn)

    todo = conn.execute(
        "SELECT required_keys, optional_keys, enum_constraints "
        "FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    assert todo is not None
    assert json.loads(todo["required_keys"]) == []
    todo_optional = json.loads(todo["optional_keys"])
    assert "files_expected" in todo_optional
    assert "acceptance_criteria" in todo_optional
    assert "required_skills" in todo_optional
    assert "multi_phase_arc" in todo_optional
    assert "multi_phase_arc" in json.loads(todo["enum_constraints"])

    plan = conn.execute(
        "SELECT optional_keys FROM type_attribute_schemas WHERE entity_type = 'plan'"
    ).fetchone()
    assert plan is not None
    plan_optional = json.loads(plan["optional_keys"])
    assert "phases" in plan_optional
    assert "required_skills" in plan_optional

    phase = conn.execute(
        "SELECT required_keys, optional_keys FROM type_attribute_schemas "
        "WHERE entity_type = 'plan_phase'"
    ).fetchone()
    assert phase is not None
    assert set(json.loads(phase["required_keys"])) == {"phase_dir", "phase_number"}


def test_collapses_entity_attribute_aliases(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, attributes) VALUES (?, ?, ?)",
        (
            "todo:t1",
            "todo",
            json.dumps(
                {
                    "files_modified": ["a.py"],
                    "acceptance": ["done"],
                    "trips_todo_plan_threshold": True,
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO entities (id, type, attributes) VALUES (?, ?, ?)",
        (
            "plan_phase:p1",
            "plan_phase",
            json.dumps({"directory": "tasks/plans/x", "phase_number": 1}),
        ),
    )

    migration_059.migrate(conn)

    todo_attrs = json.loads(
        conn.execute("SELECT attributes FROM entities WHERE id = 'todo:t1'").fetchone()[
            "attributes"
        ]
    )
    assert todo_attrs["files_expected"] == ["a.py"]
    assert todo_attrs["acceptance_criteria"] == ["done"]
    assert todo_attrs["multi_phase_arc"] is True
    assert "files_modified" not in todo_attrs
    assert "trips_todo_plan_threshold" not in todo_attrs

    phase_attrs = json.loads(
        conn.execute(
            "SELECT attributes FROM entities WHERE id = 'plan_phase:p1'"
        ).fetchone()["attributes"]
    )
    assert phase_attrs["phase_dir"] == "tasks/plans/x"
    assert "directory" not in phase_attrs


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_059.migrate(conn)
    first = conn.execute(
        "SELECT optional_keys FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    migration_059.migrate(conn)
    second = conn.execute(
        "SELECT optional_keys FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    assert json.loads(first["optional_keys"]) == json.loads(second["optional_keys"])
