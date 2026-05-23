"""Unit tests for migration 041 (skill-linkage primitives).

Applies the migration to an in-memory SQLite DB pre-seeded with the
prerequisite tables (relationship_types, session_edge_types,
type_attribute_schemas, entities, relationships) and confirms:

  * `requires` row inserted into both relationship_types and session_edge_types.
  * `required_skills` registered in type_attribute_schemas as an optional
    key for project / plan / plan_phase entity types.
  * Existing `depends_on` rows from project / plan / plan_phase entities
    to agent_skill entities are re-typed to `requires`. depends_on rows
    from other entity types (e.g. case → agent_skill) are left untouched.
  * Re-running the migration is a no-op.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = Path(__file__).parent / "migrations" / "041_skill_linkage_primitives.py"
_spec = importlib.util.spec_from_file_location(
    "migration_041_skill_linkage_primitives", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_041 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_041)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT
        );
        CREATE TABLE session_edge_types (
            type TEXT PRIMARY KEY,
            description TEXT,
            directional INTEGER DEFAULT 1
        );
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
            name TEXT,
            attributes TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        INSERT INTO relationship_types (type, description) VALUES
            ('depends_on', 'Source depends on target');

        INSERT INTO entities (id, type, name) VALUES
            ('project:p1', 'project', 'P1'),
            ('plan:pl1', 'plan', 'Pl1'),
            ('plan_phase:pp1', 'plan_phase', 'Pp1'),
            ('case:c1', 'case', 'C1'),
            ('agent_skill:s1', 'agent_skill', 'S1'),
            ('agent_skill:s2', 'agent_skill', 'S2'),
            ('agent_skill:s3', 'agent_skill', 'S3'),
            ('agent_skill:s4', 'agent_skill', 'S4');

        -- Manifest-shaped depends_on rows that should migrate to requires.
        INSERT INTO relationships (type, from_entity, to_entity) VALUES
            ('depends_on', 'project:p1', 'agent_skill:s1'),
            ('depends_on', 'project:p1', 'agent_skill:s2'),
            ('depends_on', 'plan:pl1', 'agent_skill:s3'),
            ('depends_on', 'plan_phase:pp1', 'agent_skill:s4'),
            -- Non-manifest depends_on rows that must NOT be migrated.
            ('depends_on', 'case:c1', 'agent_skill:s1'),
            ('depends_on', 'project:p1', 'plan:pl1');
        """
    )
    return c


def test_registers_relationship_and_edge_types(conn: sqlite3.Connection) -> None:
    migration_041.migrate(conn)

    rel = conn.execute(
        "SELECT description FROM relationship_types WHERE type = 'requires'"
    ).fetchone()
    assert rel is not None
    assert "agent_skill" in rel["description"]

    edge = conn.execute(
        "SELECT description, directional FROM session_edge_types "
        "WHERE type = 'requires'"
    ).fetchone()
    assert edge is not None
    assert edge["directional"] == 1


def test_registers_required_skills_schema_on_three_types(
    conn: sqlite3.Connection,
) -> None:
    migration_041.migrate(conn)

    for entity_type in ("project", "plan", "plan_phase"):
        row = conn.execute(
            "SELECT required_keys, optional_keys, notes "
            "FROM type_attribute_schemas WHERE entity_type = ?",
            (entity_type,),
        ).fetchone()
        assert row is not None, f"missing schema row for {entity_type}"
        assert "required_skills" in json.loads(row["optional_keys"])
        assert json.loads(row["required_keys"]) == []
        assert "agent_skill" in row["notes"]


def test_preserves_existing_attribute_schema_when_present(
    conn: sqlite3.Connection,
) -> None:
    """Pre-existing schema entries keep their required keys + notes."""
    conn.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "project",
            json.dumps(["title"]),
            json.dumps(["owner"]),
            json.dumps({}),
            "Pre-existing notes.",
        ),
    )

    migration_041.migrate(conn)

    row = conn.execute(
        "SELECT required_keys, optional_keys, notes "
        "FROM type_attribute_schemas WHERE entity_type = 'project'"
    ).fetchone()
    assert json.loads(row["required_keys"]) == ["title"]
    optional = json.loads(row["optional_keys"])
    assert "owner" in optional
    assert "required_skills" in optional
    assert "Pre-existing notes." in row["notes"]


def test_migrates_depends_on_to_requires_for_manifest_rows(
    conn: sqlite3.Connection,
) -> None:
    migration_041.migrate(conn)

    requires_rows = conn.execute(
        "SELECT from_entity, to_entity FROM relationships "
        "WHERE type = 'requires' ORDER BY from_entity, to_entity"
    ).fetchall()
    migrated = {(r["from_entity"], r["to_entity"]) for r in requires_rows}
    assert migrated == {
        ("plan:pl1", "agent_skill:s3"),
        ("plan_phase:pp1", "agent_skill:s4"),
        ("project:p1", "agent_skill:s1"),
        ("project:p1", "agent_skill:s2"),
    }


def test_leaves_non_manifest_depends_on_rows_alone(
    conn: sqlite3.Connection,
) -> None:
    migration_041.migrate(conn)

    untouched = conn.execute(
        "SELECT from_entity, to_entity FROM relationships "
        "WHERE type = 'depends_on' ORDER BY from_entity, to_entity"
    ).fetchall()
    remaining = {(r["from_entity"], r["to_entity"]) for r in untouched}
    # case→skill (non-manifest source type) and project→plan (non-skill target)
    # MUST stay as depends_on.
    assert remaining == {
        ("case:c1", "agent_skill:s1"),
        ("project:p1", "plan:pl1"),
    }


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_041.migrate(conn)
    first_count = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE type = 'requires'"
    ).fetchone()[0]

    migration_041.migrate(conn)

    second_count = conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE type = 'requires'"
    ).fetchone()[0]
    assert first_count == second_count == 4

    rel_types = conn.execute(
        "SELECT COUNT(*) FROM relationship_types WHERE type = 'requires'"
    ).fetchone()[0]
    assert rel_types == 1
