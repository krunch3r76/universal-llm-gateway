"""Unit tests for the required_skills⟷requires audit detector, extended to
the `todo` entity type (todo:todo-required-skills-audit-detector).

Mirrors the project/plan/plan_phase contract for todo and exercises the
migration-045 normalization (bare slug + optional ``#section`` anchor →
``agent_skill:<slug>``) plus inverse drift (a ``requires`` edge to an
agent_skill absent from the manifest). Verifies the existing
project-type behavior is preserved (scope extension, not replacement).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.project import (
    _normalize_skill_ref,
    detect_project_required_skills_no_relationship,
)

_KIND = "project_required_skills_no_relationship"


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


def _add_todo(conn, todo_id, required_skills):
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'todo', ?, ?)",
        (todo_id, todo_id, json.dumps({"required_skills": required_skills})),
    )


def _add_requires(conn, from_id, to_id, active=1):
    conn.execute(
        "INSERT INTO relationships (type, from_entity, to_entity, active) "
        "VALUES ('requires', ?, ?, ?)",
        (from_id, to_id, active),
    )


# --- normalization unit ----------------------------------------------------


def test_normalize_full_id() -> None:
    assert _normalize_skill_ref("agent_skill:foo-bar") == "agent_skill:foo-bar"


def test_normalize_bare_slug() -> None:
    assert _normalize_skill_ref("architecture-invariants") == (
        "agent_skill:architecture-invariants"
    )


def test_normalize_bare_slug_with_section_anchor() -> None:
    assert _normalize_skill_ref("skill-document-writing#audit-gate-response") == (
        "agent_skill:skill-document-writing"
    )


def test_normalize_rejects_non_string_and_bad_slug() -> None:
    assert _normalize_skill_ref(123) is None
    assert _normalize_skill_ref("Bad Slug!") is None
    assert _normalize_skill_ref("") is None


# --- worked-example shape (must NOT flag) ----------------------------------


def test_worked_example_shape_no_finding(conn: sqlite3.Connection) -> None:
    """Bare-slug + #section attribute paired with a full-id requires edge."""
    _add_todo(
        conn,
        "todo:agent-skill-skill-binding-backfill",
        ["skill-document-writing#audit-gate-response"],
    )
    _add_requires(
        conn,
        "todo:agent-skill-skill-binding-backfill",
        "agent_skill:skill-document-writing",
    )
    findings = detect_project_required_skills_no_relationship(conn)
    assert findings == []


# --- forward drift ---------------------------------------------------------


def test_todo_attribute_without_edge_one_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["architecture-invariants"])
    findings = detect_project_required_skills_no_relationship(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _KIND
    assert f["subject"] == "todo:t1"
    assert "agent_skill:architecture-invariants" in f["detail"]


def test_bare_slug_with_matching_edge_no_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["architecture-invariants"])
    _add_requires(conn, "todo:t1", "agent_skill:architecture-invariants")
    assert detect_project_required_skills_no_relationship(conn) == []


def test_full_id_form_on_todo_with_edge_no_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["agent_skill:architecture-invariants"])
    _add_requires(conn, "todo:t1", "agent_skill:architecture-invariants")
    assert detect_project_required_skills_no_relationship(conn) == []


# --- inverse drift ---------------------------------------------------------


def test_inverse_edge_without_attribute_entry_one_finding(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(conn, "todo:t1", ["architecture-invariants"])
    _add_requires(conn, "todo:t1", "agent_skill:architecture-invariants")
    _add_requires(conn, "todo:t1", "agent_skill:orphan-skill")
    findings = detect_project_required_skills_no_relationship(conn)
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:t1"
    assert "agent_skill:orphan-skill" in findings[0]["detail"]
    assert "absent from its required_skills" in findings[0]["detail"]


def test_requires_edge_with_no_attribute_not_flagged(
    conn: sqlite3.Connection,
) -> None:
    """Guard: an entity with a `requires` edge but NO required_skills
    attribute is not drift — there is no manifest to be inconsistent with
    (protects 041-backfilled depends_on→requires edges)."""
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) "
        "VALUES ('todo:t1', 'todo', 't1', ?)",
        (json.dumps({"priority": "high"}),),
    )
    _add_requires(conn, "todo:t1", "agent_skill:some-skill")
    assert detect_project_required_skills_no_relationship(conn) == []


# --- malformed -------------------------------------------------------------


def test_malformed_entry_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["Bad Slug!"])
    findings = detect_project_required_skills_no_relationship(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == _KIND
    assert "does not resolve" in findings[0]["detail"]


def test_inactive_edge_does_not_satisfy(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["architecture-invariants"])
    _add_requires(conn, "todo:t1", "agent_skill:architecture-invariants", active=0)
    findings = detect_project_required_skills_no_relationship(conn)
    assert len(findings) == 1  # forward drift: inactive edge ignored


# --- scope: project type still works (regression) --------------------------


def test_project_type_still_detected(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) "
        "VALUES ('project:p1', 'project', 'P1', ?)",
        (json.dumps({"required_skills": ["agent_skill:s1"]}),),
    )
    findings = detect_project_required_skills_no_relationship(conn)
    assert len(findings) == 1
    assert findings[0]["subject"] == "project:p1"

    _add_requires(conn, "project:p1", "agent_skill:s1")
    assert detect_project_required_skills_no_relationship(conn) == []


# --- subject filter --------------------------------------------------------


def test_subject_filter_scopes_to_one_entity(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", ["architecture-invariants"])
    _add_todo(conn, "todo:t2", ["prose-discipline"])
    findings = detect_project_required_skills_no_relationship(conn, subject="todo:t1")
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:t1"
