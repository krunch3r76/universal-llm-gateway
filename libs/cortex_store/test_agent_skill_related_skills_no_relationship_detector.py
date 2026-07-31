"""Unit tests for the related_skills⟷references/related_to audit detector."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.agent_skill import (
    _normalize_related_slug,
    detect_agent_skill_related_skills_no_relationship,
)
from cortex_store.dispatch_ops._detectors.project import (
    detect_project_required_skills_no_relationship,
)
from cortex_store.dispatch_ops._detectors.todo import (
    detect_todo_implementation_seed_incomplete,
)

_KIND = "agent_skill_related_skills_no_relationship"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            source_uri TEXT,
            workflow_state TEXT,
            attributes TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    return c


def _add_skill(conn, slug, related_skills):
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'agent_skill', ?, ?)",
        (
            f"agent_skill:{slug}",
            slug,
            json.dumps({"related_skills": related_skills}),
        ),
    )


def _add_edge(conn, from_id, to_id, rel_type, active=1):
    conn.execute(
        "INSERT INTO relationships (type, from_entity, to_entity, active) "
        "VALUES (?, ?, ?, ?)",
        (rel_type, from_id, to_id, active),
    )


def test_normalize_bare_slug() -> None:
    # arc 3924: normalize returns the bare slug (prefix-agnostic edge matching).
    assert _normalize_related_slug("architecture-invariants") == (
        "architecture-invariants"
    )


def test_normalize_full_id() -> None:
    assert _normalize_related_slug("agent_skill:foo-bar") == "foo-bar"


def test_normalize_rule_and_skill_prefixes() -> None:
    # Post-migration companions may be typed rule:/skill: — all strip to bare slug.
    assert _normalize_related_slug("rule:cortex-orientation") == "cortex-orientation"
    assert _normalize_related_slug("skill:implement-work-item") == "implement-work-item"


def test_retyped_rule_companion_edge_satisfies(conn: sqlite3.Connection) -> None:
    # arc 3924: a companion retyped agent_skill→rule has its edge endpoint
    # rewritten to rule:<slug>; bare-slug matching must still consider it wired
    # (no false-positive drift finding).
    _add_skill(conn, "consult-routing", ["friction-review"])
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "rule:friction-review",
        "references",
    )
    findings = detect_agent_skill_related_skills_no_relationship(conn)
    assert findings == []


def test_attribute_without_edge_one_finding(conn: sqlite3.Connection) -> None:
    _add_skill(conn, "consult-routing", ["friction-review"])
    findings = detect_agent_skill_related_skills_no_relationship(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == _KIND
    assert findings[0]["subject"] == "agent_skill:consult-routing"


def test_references_edge_satisfies(conn: sqlite3.Connection) -> None:
    _add_skill(conn, "consult-routing", ["friction-review"])
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "agent_skill:friction-review",
        "references",
    )
    assert detect_agent_skill_related_skills_no_relationship(conn) == []


def test_related_to_canonical_edge_satisfies(conn: sqlite3.Connection) -> None:
    _add_skill(conn, "refine-pipeline", ["build-pipeline"])
    lo, hi = sorted(["agent_skill:build-pipeline", "agent_skill:refine-pipeline"])
    _add_edge(conn, lo, hi, "related_to")
    assert detect_agent_skill_related_skills_no_relationship(conn) == []


def test_inverse_edge_without_attribute_entry(conn: sqlite3.Connection) -> None:
    _add_skill(conn, "consult-routing", ["friction-review"])
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "agent_skill:friction-review",
        "references",
    )
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "agent_skill:orphan-skill",
        "references",
    )
    findings = detect_agent_skill_related_skills_no_relationship(conn)
    assert len(findings) == 1
    assert "orphan-skill" in findings[0]["detail"]


def test_skill_edge_does_not_affect_project_detector(conn: sqlite3.Connection) -> None:
    _add_skill(conn, "consult-routing", ["friction-review"])
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "agent_skill:friction-review",
        "references",
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) "
        "VALUES ('todo:t1', 'todo', 't1', ?)",
        (json.dumps({"required_skills": ["architecture-invariants"]}),),
    )
    _add_edge(
        conn,
        "todo:t1",
        "agent_skill:architecture-invariants",
        "requires",
    )
    assert detect_project_required_skills_no_relationship(conn) == []


def test_skill_edge_does_not_affect_todo_seed_detector(
    conn: sqlite3.Connection,
) -> None:
    conn.executescript(
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
    conn.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES ('todo', '[]', '[\"required_skills\"]', '{}', 'test')",
    )
    _add_skill(conn, "consult-routing", ["friction-review"])
    _add_edge(
        conn,
        "agent_skill:consult-routing",
        "agent_skill:friction-review",
        "references",
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, workflow_state, attributes) "
        "VALUES ('todo:t1', 'todo', 't1', 'workspaces://x', 'in_progress', ?)",
        (json.dumps({"required_skills": ["architecture-invariants"]}),),
    )
    _add_edge(conn, "todo:t1", "project:p1", "child_of")
    assert detect_todo_implementation_seed_incomplete(conn) == []
