"""Unit tests for detect_todo_dense_spec_attributes_unpopulated."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.todo import (
    detect_todo_dense_spec_attributes_unpopulated,
)
from cortex_store.dispatch_ops.ops_audit_detectors import (
    GRAPH_ONLY_KINDS,
    SEVERITY,
    get_all_detectors,
)

_KIND = "todo_dense_spec_attributes_unpopulated"


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
        """
    )
    return c


def _add_todo(
    conn: sqlite3.Connection,
    todo_id: str,
    *,
    workflow_state: str = "in_progress",
    source_uri: str | None = "tasks/specs/t1.md",
    attrs: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, workflow_state, attributes) "
        "VALUES (?, 'todo', ?, ?, ?, ?)",
        (
            todo_id,
            todo_id,
            source_uri,
            workflow_state,
            json.dumps(attrs) if attrs else None,
        ),
    )


@pytest.mark.offline
def test_fires_when_implement_ready_but_attrs_unpopulated(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t1",
        attrs={
            "density_triage": "judgment_required",
            "implement_ready_assertion_id": 99,
        },
    )
    findings = detect_todo_dense_spec_attributes_unpopulated(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == _KIND
    assert findings[0]["severity"] == "warning"
    assert "files_expected" in findings[0]["detail"]
    assert "acceptance_criteria" in findings[0]["detail"]


@pytest.mark.offline
def test_suppressed_by_attributes_distillation_waived(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t2",
        attrs={
            "density_triage": "judgment_required",
            "implement_ready_assertion_id": 99,
            "attributes_distillation_waived": "legacy todo",
        },
    )
    assert detect_todo_dense_spec_attributes_unpopulated(conn) == []


@pytest.mark.offline
def test_no_fire_when_attrs_populated(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t3",
        attrs={
            "density_triage": "judgment_required",
            "implement_ready_assertion_id": 99,
            "files_expected": ["a.py"],
            "acceptance_criteria": ["done"],
        },
    )
    assert detect_todo_dense_spec_attributes_unpopulated(conn) == []


@pytest.mark.offline
def test_no_fire_without_implement_ready_assertion_id(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t4",
        attrs={"density_triage": "judgment_required"},
    )
    assert detect_todo_dense_spec_attributes_unpopulated(conn) == []


@pytest.mark.offline
def test_registered_graph_only_warning() -> None:
    assert _KIND in GRAPH_ONLY_KINDS
    assert SEVERITY[_KIND] == "warning"
    assert _KIND in get_all_detectors()
