"""Unit tests for entity_vocabulary_grammar and entity_structural_antipattern detectors.

Covers G1/G2/G3/A2 firing, active=0 no-fire, self-edge once,
portfolio_child_of JSON-bool-only suppression, and subject filter.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.entity_grammar import (
    detect_entity_structural_antipattern,
    detect_entity_vocabulary_grammar,
)
from cortex_store.dispatch_ops.ops_audit_detectors import (
    ALL_KINDS,
    FS_TOUCHING_KINDS,
    GRAPH_ONLY_KINDS,
    get_all_detectors,
)

_VOCAB_KIND = "entity_vocabulary_grammar"
_ANTIPATTERN_KIND = "entity_structural_antipattern"


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
            attributes TEXT,
            workflow_state TEXT,
            status TEXT,
            description TEXT
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


def _add_entity(
    conn,
    entity_id: str,
    entity_type: str,
    *,
    attributes: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, ?, ?, ?)",
        (
            entity_id,
            entity_type,
            entity_id,
            json.dumps(attributes) if attributes is not None else None,
        ),
    )


def _add_rel(
    conn,
    from_id: str,
    to_id: str,
    rel_type: str = "child_of",
    *,
    active: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO relationships (type, from_entity, to_entity, active) "
        "VALUES (?, ?, ?, ?)",
        (rel_type, from_id, to_id, active),
    )


# --- G1 ------------------------------------------------------------------


def test_g1_step_entity_fires(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "step:inline-step", "step")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _VOCAB_KIND
    assert f["subject"] == "step:inline-step"
    assert f["severity"] == "warning"
    assert f["detail"].startswith("G1:")


def test_g1_id_prefix_without_step_type_fires(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "step:orphan-prefix", "todo")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    assert findings[0]["subject"] == "step:orphan-prefix"
    assert findings[0]["detail"].startswith("G1:")


# --- G2 ------------------------------------------------------------------


def test_g2_plan_phase_child_of_task_fires(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "plan_phase:phase-1", "plan_phase")
    _add_entity(conn, "task:root", "task")
    _add_rel(conn, "plan_phase:phase-1", "task:root")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _VOCAB_KIND
    assert f["subject"] == "plan_phase:phase-1"
    assert f["severity"] == "warning"
    assert f["detail"].startswith("G2:")
    assert "plan_phase:phase-1" in f["detail"]
    assert "task:root" in f["detail"]


# --- G3 ------------------------------------------------------------------


def test_g3_todo_child_of_todo_fires(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "todo:child", "todo")
    _add_entity(conn, "todo:parent", "todo")
    _add_rel(conn, "todo:child", "todo:parent")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _VOCAB_KIND
    assert f["subject"] == "todo:child"
    assert f["severity"] == "warning"
    assert f["detail"].startswith("G3:")
    assert "todo:child" in f["detail"]
    assert "todo:parent" in f["detail"]


# --- A2 ------------------------------------------------------------------


def test_a2_task_child_of_project_fires(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "task:child-task", "task")
    _add_entity(conn, "project:portfolio", "project")
    _add_rel(conn, "task:child-task", "project:portfolio")
    findings = detect_entity_structural_antipattern(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _ANTIPATTERN_KIND
    assert f["subject"] == "task:child-task"
    assert f["severity"] == "warning"
    assert f["detail"].startswith("A2:")
    assert "task:child-task" in f["detail"]
    assert "project:portfolio" in f["detail"]


# --- clean control -------------------------------------------------------


def test_clean_graph_no_findings(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "todo:leaf", "todo")
    _add_entity(conn, "task:root", "task")
    _add_entity(conn, "project:p1", "project")
    _add_rel(conn, "todo:leaf", "task:root")
    assert detect_entity_vocabulary_grammar(conn) == []
    assert detect_entity_structural_antipattern(conn) == []


# --- active=0 -------------------------------------------------------------


def test_inactive_edge_does_not_fire_g3(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "todo:child", "todo")
    _add_entity(conn, "todo:parent", "todo")
    _add_rel(conn, "todo:child", "todo:parent", active=0)
    assert detect_entity_vocabulary_grammar(conn) == []


def test_inactive_edge_does_not_fire_a2(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "task:child-task", "task")
    _add_entity(conn, "project:portfolio", "project")
    _add_rel(conn, "task:child-task", "project:portfolio", active=0)
    assert detect_entity_structural_antipattern(conn) == []


# --- self-edge ------------------------------------------------------------


def test_self_edge_fires_exactly_once(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "todo:self", "todo")
    _add_rel(conn, "todo:self", "todo:self")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:self"
    assert findings[0]["detail"].startswith("G3:")


# --- A2 portfolio_child_of suppression -----------------------------------


def test_portfolio_child_of_json_true_suppresses_a2(conn: sqlite3.Connection) -> None:
    _add_entity(
        conn,
        "task:portfolio-task",
        "task",
        attributes={"portfolio_child_of": True},
    )
    _add_entity(conn, "project:portfolio", "project")
    _add_rel(conn, "task:portfolio-task", "project:portfolio")
    assert detect_entity_structural_antipattern(conn) == []


def test_portfolio_child_of_string_true_does_not_suppress(
    conn: sqlite3.Connection,
) -> None:
    _add_entity(
        conn,
        "task:portfolio-task",
        "task",
        attributes={"portfolio_child_of": "true"},
    )
    _add_entity(conn, "project:portfolio", "project")
    _add_rel(conn, "task:portfolio-task", "project:portfolio")
    findings = detect_entity_structural_antipattern(conn)
    assert len(findings) == 1
    assert findings[0]["detail"].startswith("A2:")


def test_portfolio_child_of_does_not_suppress_g3(conn: sqlite3.Connection) -> None:
    _add_entity(
        conn,
        "todo:child",
        "todo",
        attributes={"portfolio_child_of": True},
    )
    _add_entity(conn, "todo:parent", "todo")
    _add_rel(conn, "todo:child", "todo:parent")
    findings = detect_entity_vocabulary_grammar(conn)
    assert len(findings) == 1
    assert findings[0]["detail"].startswith("G3:")


# --- subject filter -------------------------------------------------------


def test_subject_filter_returns_incident_findings_only(
    conn: sqlite3.Connection,
) -> None:
    _add_entity(conn, "todo:child-a", "todo")
    _add_entity(conn, "todo:parent-a", "todo")
    _add_entity(conn, "todo:child-b", "todo")
    _add_entity(conn, "todo:parent-b", "todo")
    _add_rel(conn, "todo:child-a", "todo:parent-a")
    _add_rel(conn, "todo:child-b", "todo:parent-b")

    findings = detect_entity_vocabulary_grammar(conn, subject="todo:child-a")
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:child-a"

    findings_to = detect_entity_vocabulary_grammar(conn, subject="todo:parent-a")
    assert len(findings_to) == 1
    assert findings_to[0]["subject"] == "todo:child-a"


def test_subject_filter_clean_id_returns_empty(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "todo:child", "todo")
    _add_entity(conn, "todo:parent", "todo")
    _add_rel(conn, "todo:child", "todo:parent")
    assert detect_entity_vocabulary_grammar(conn, subject="todo:clean") == []
    assert detect_entity_structural_antipattern(conn, subject="task:clean") == []


def test_a2_subject_filter_scopes_to_task(conn: sqlite3.Connection) -> None:
    _add_entity(conn, "task:t1", "task")
    _add_entity(conn, "task:t2", "task")
    _add_entity(conn, "project:p1", "project")
    _add_entity(conn, "project:p2", "project")
    _add_rel(conn, "task:t1", "project:p1")
    _add_rel(conn, "task:t2", "project:p2")
    findings = detect_entity_structural_antipattern(conn, subject="task:t1")
    assert len(findings) == 1
    assert findings[0]["subject"] == "task:t1"


# --- registration / dispatch ---------------------------------------------


def test_kinds_registered_in_taxonomy() -> None:
    assert _VOCAB_KIND in GRAPH_ONLY_KINDS
    assert _ANTIPATTERN_KIND in GRAPH_ONLY_KINDS
    assert _VOCAB_KIND in ALL_KINDS
    assert _ANTIPATTERN_KIND in ALL_KINDS
    assert _VOCAB_KIND not in FS_TOUCHING_KINDS
    assert _ANTIPATTERN_KIND not in FS_TOUCHING_KINDS


def test_get_all_detectors_includes_both() -> None:
    detectors = get_all_detectors()
    assert _VOCAB_KIND in detectors
    assert _ANTIPATTERN_KIND in detectors


def test_get_all_detectors_callables_dispatch_both_kinds(
    conn: sqlite3.Connection,
) -> None:
    _add_entity(conn, "todo:child", "todo")
    _add_entity(conn, "todo:parent", "todo")
    _add_entity(conn, "task:child-task", "task")
    _add_entity(conn, "project:portfolio", "project")
    _add_rel(conn, "todo:child", "todo:parent")
    _add_rel(conn, "task:child-task", "project:portfolio")

    detectors = get_all_detectors()
    vocab_findings = detectors[_VOCAB_KIND](conn)
    antipattern_findings = detectors[_ANTIPATTERN_KIND](conn)
    assert len(vocab_findings) == 1
    assert vocab_findings[0]["kind"] == _VOCAB_KIND
    assert len(antipattern_findings) == 1
    assert antipattern_findings[0]["kind"] == _ANTIPATTERN_KIND
