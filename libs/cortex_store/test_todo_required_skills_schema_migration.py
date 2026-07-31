"""Unit tests for migration 045 (todo required_skills attribute schema).

Applies the migration to an in-memory SQLite DB pre-seeded with the
``type_attribute_schemas`` table and confirms:

  * ``required_skills`` registered as an OPTIONAL key for the ``todo``
    entity type (mirrors migration 041's project/plan/plan_phase
    registration, scoped to todo per migration 045).
  * A pre-existing ``todo`` schema row keeps its required/optional keys
    and notes; ``required_skills`` is appended to optional.
  * Re-running the migration is a no-op (idempotent).
  * Absence of the ``type_attribute_schemas`` table degrades gracefully
    (pre-037 sandbox).
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = Path(__file__).parent / "migrations" / "045_todo_required_skills_schema.py"
_spec = importlib.util.spec_from_file_location(
    "migration_045_todo_required_skills_schema", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_045 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_045)


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
        """
    )
    return c


def test_registers_required_skills_schema_on_todo(conn: sqlite3.Connection) -> None:
    migration_045.migrate(conn)

    row = conn.execute(
        "SELECT required_keys, optional_keys, notes "
        "FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    assert row is not None, "missing schema row for todo"
    assert "required_skills" in json.loads(row["optional_keys"])
    assert json.loads(row["required_keys"]) == []
    assert "agent_skill" in row["notes"]


def test_required_skills_is_optional_not_required(conn: sqlite3.Connection) -> None:
    """Resolved fork: optional key + detector teeth, not hard-blocking."""
    migration_045.migrate(conn)
    row = conn.execute(
        "SELECT required_keys, optional_keys "
        "FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    assert "required_skills" not in json.loads(row["required_keys"])
    assert "required_skills" in json.loads(row["optional_keys"])


def test_preserves_existing_todo_schema_when_present(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "todo",
            json.dumps(["priority"]),
            json.dumps(["domain"]),
            json.dumps({}),
            "Pre-existing todo notes.",
        ),
    )

    migration_045.migrate(conn)

    row = conn.execute(
        "SELECT required_keys, optional_keys, notes "
        "FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()
    assert json.loads(row["required_keys"]) == ["priority"]
    optional = json.loads(row["optional_keys"])
    assert "domain" in optional
    assert "required_skills" in optional
    assert "Pre-existing todo notes." in row["notes"]


def test_does_not_touch_other_types(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        ("project", json.dumps([]), json.dumps([]), json.dumps({}), "proj"),
    )
    migration_045.migrate(conn)
    row = conn.execute(
        "SELECT optional_keys FROM type_attribute_schemas WHERE entity_type = 'project'"
    ).fetchone()
    # 045 only registers the todo row; project is untouched here.
    assert json.loads(row["optional_keys"]) == []


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_045.migrate(conn)
    first = conn.execute(
        "SELECT optional_keys, notes FROM type_attribute_schemas "
        "WHERE entity_type = 'todo'"
    ).fetchone()

    migration_045.migrate(conn)
    second = conn.execute(
        "SELECT optional_keys, notes FROM type_attribute_schemas "
        "WHERE entity_type = 'todo'"
    ).fetchone()

    assert json.loads(first["optional_keys"]) == json.loads(second["optional_keys"])
    assert json.loads(second["optional_keys"]).count("required_skills") == 1
    assert first["notes"] == second["notes"]

    count = conn.execute(
        "SELECT COUNT(*) FROM type_attribute_schemas WHERE entity_type = 'todo'"
    ).fetchone()[0]
    assert count == 1


def test_no_table_is_noop() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # No type_attribute_schemas table — must not raise.
    migration_045.migrate(c)
