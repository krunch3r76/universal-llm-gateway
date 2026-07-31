"""Unit tests for the advisory todo implement-readiness risk detector."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cortex_store.dispatch_ops._detectors import todo_density_risk
from cortex_store.dispatch_ops._detectors.todo_density_risk import (
    detect_todo_implement_readiness_risk,
)

_KIND = "todo_implement_readiness_risk"


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
    workflow_state: str = "open",
    source_uri: str | None = None,
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
def test_stub_not_dense_when_judgment_without_assertion(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        attrs={"density_triage": "judgment_required"},
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert findings[0]["kind"] == _KIND
    assert findings[0]["severity"] == "warning"
    assert "stub_not_dense" in findings[0]["detail"]


@pytest.mark.offline
def test_mechanical_but_design_skills(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t2",
        source_uri="tasks/specs/t2.md",
        attrs={
            "density_triage": "mechanical",
            "required_skills": ["architecture-invariants", "build-pipeline"],
        },
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert "mechanical_but_design_skills" in findings[0]["detail"]


@pytest.mark.offline
def test_forks_open_reads_spec_file(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir = tmp_path / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    spec_path = spec_dir / "t3.md"
    spec_path.write_text(
        "# Spec\n\n## 1. Problem\n\nProblem.\n\nFork OPEN: unresolved placement.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(todo_density_risk, "_FILES_ROOT", tmp_path)
    _add_todo(
        conn,
        "todo:t3",
        source_uri="tasks/specs/t3.md",
        attrs={"density_triage": "mechanical"},
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert "forks_open" in findings[0]["detail"]


@pytest.mark.offline
def test_spec_not_dense_when_judgment_required(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir = tmp_path / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "t6.md").write_text(
        "# Sparse\n\n## 1. Problem\n\nOnly problem.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(todo_density_risk, "_FILES_ROOT", tmp_path)
    _add_todo(
        conn,
        "todo:t6",
        source_uri="tasks/specs/t6.md",
        attrs={
            "density_triage": "judgment_required",
            "implement_ready_assertion_id": 1,
        },
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert "spec_not_dense" in findings[0]["detail"]


@pytest.mark.offline
def test_spec_soft_incomplete_warns_on_tbd(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_dir = tmp_path / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "t7.md").write_text(
        "# Spec\n\n## 1. Problem\n\nTBD details later.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(todo_density_risk, "_FILES_ROOT", tmp_path)
    _add_todo(
        conn,
        "todo:t7",
        source_uri="tasks/specs/t7.md",
        attrs={"density_triage": "mechanical"},
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert "spec_soft_incomplete" in findings[0]["detail"]


@pytest.mark.offline
def test_suppression_flags_do_not_silence_detector(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t4",
        source_uri="tasks/specs/t4.md",
        attrs={
            "density_triage": "judgment_required",
            "backlog": True,
            "seed_contract_ack": "documented",
        },
    )
    findings = detect_todo_implement_readiness_risk(conn)
    assert findings
    assert findings[0]["kind"] == _KIND


@pytest.mark.offline
def test_clean_todo_emits_no_finding(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t5",
        source_uri="tasks/specs/t5.md",
        attrs={
            "density_triage": "mechanical",
            "required_skills": [
                "architecture-invariants",
                "ulg-architecture",
                "docstring-quality",
            ],
            "implement_ready_assertion_id": 1,
        },
    )
    assert detect_todo_implement_readiness_risk(conn) == []
