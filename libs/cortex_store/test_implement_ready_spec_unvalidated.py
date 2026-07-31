"""Unit tests for implement_ready_spec_unvalidated audit detector (friction 20198)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from implement_admission.dense_spec_schema import dense_spec_hash_uri

from cortex_store.dispatch_ops._detectors import implement_ready_spec
from cortex_store.dispatch_ops._detectors.implement_ready_spec import (
    detect_implement_ready_spec_unvalidated,
)
from cortex_store.dispatch_ops.ops_audit_detectors import (
    FS_TOUCHING_KINDS,
    SEVERITY,
    get_all_detectors,
)

_KIND = "implement_ready_spec_unvalidated"
_TODO = "todo:test-slug"
_SPEC = "tasks/specs/test-slug.md"
_PREDICATE = f"status({_TODO}, implement_ready, current)"

_VALID_SPEC = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the validator.

## 7. Acceptance criteria

1. Validator passes dense specs.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""

_INVALID_SPEC = """\
# Sparse spec

## 1. Problem

Only problem section.
"""


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
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            predicate_form TEXT,
            evidence_uris TEXT,
            superseded_by INTEGER,
            valid_until TEXT
        );
        """
    )
    return c


def _add_todo(
    conn: sqlite3.Connection,
    todo_id: str = _TODO,
    *,
    attrs: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes) VALUES (?, 'todo', ?, ?)",
        (todo_id, todo_id, json.dumps(attrs) if attrs else None),
    )


def _add_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str = _TODO,
    predicate_form: str | None = _PREDICATE,
    evidence_uris: list[str] | None = None,
    superseded_by: int | None = None,
    valid_until: str | None = None,
    claim: str = "Implement-ready for dispatch.",
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions "
        "(entity_id, claim, predicate_form, evidence_uris, superseded_by, valid_until) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            claim,
            predicate_form,
            json.dumps(evidence_uris) if evidence_uris is not None else None,
            superseded_by,
            valid_until,
        ),
    )
    return int(cur.lastrowid)


def _write_spec(tmp_path: Path, text: str, *, rel: str = _SPEC) -> None:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _evidence_for(text: str) -> list[str]:
    return [_SPEC, dense_spec_hash_uri(text)]


@pytest.mark.offline
def test_clean_implement_ready_no_finding(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _VALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(conn, evidence_uris=_evidence_for(_VALID_SPEC))
    assert detect_implement_ready_spec_unvalidated(conn) == []


@pytest.mark.offline
def test_invalid_spec_emits_finding_with_schema_code(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _INVALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    aid = _add_assertion(conn, evidence_uris=_evidence_for(_INVALID_SPEC))
    findings = detect_implement_ready_spec_unvalidated(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _KIND
    assert f["subject"] == _TODO
    assert f["severity"] == "warning"
    assert "dense_spec_sections_missing" in f["detail"]
    assert str(aid) in f["detail"]


@pytest.mark.offline
def test_sha_drift_emits_finding(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _VALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(
        conn,
        evidence_uris=[_SPEC, dense_spec_hash_uri(_INVALID_SPEC)],
    )
    findings = detect_implement_ready_spec_unvalidated(conn)
    assert len(findings) == 1
    assert "spec_sha256" in findings[0]["detail"]


@pytest.mark.offline
def test_missing_sha_token_emits_finding(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _VALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(conn, evidence_uris=[_SPEC])
    findings = detect_implement_ready_spec_unvalidated(conn)
    assert len(findings) == 1
    assert "spec_sha256" in findings[0]["detail"]


@pytest.mark.offline
def test_missing_spec_file_emits_finding(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(conn, evidence_uris=_evidence_for(_VALID_SPEC))
    findings = detect_implement_ready_spec_unvalidated(conn)
    assert len(findings) == 1
    assert "missing or unreadable" in findings[0]["detail"]


@pytest.mark.offline
def test_superseded_assertion_skipped(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _INVALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(
        conn,
        evidence_uris=_evidence_for(_INVALID_SPEC),
        superseded_by=999,
    )
    assert detect_implement_ready_spec_unvalidated(conn) == []


@pytest.mark.offline
def test_expired_assertion_skipped(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _INVALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, attrs={"dense_spec_path": _SPEC})
    _add_assertion(
        conn,
        evidence_uris=_evidence_for(_INVALID_SPEC),
        valid_until="2000-01-01T00:00:00+00:00",
    )
    assert detect_implement_ready_spec_unvalidated(conn) == []


@pytest.mark.offline
def test_subject_filter_scopes_to_one_entity(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _INVALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn, "todo:one", attrs={"dense_spec_path": _SPEC})
    _add_todo(conn, "todo:two", attrs={"dense_spec_path": _SPEC})
    _add_assertion(
        conn,
        entity_id="todo:one",
        predicate_form="status(todo:one, implement_ready, current)",
        evidence_uris=_evidence_for(_INVALID_SPEC),
    )
    _add_assertion(
        conn,
        entity_id="todo:two",
        predicate_form="status(todo:two, implement_ready, current)",
        evidence_uris=_evidence_for(_INVALID_SPEC),
    )
    findings = detect_implement_ready_spec_unvalidated(conn, subject="todo:one")
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:one"


@pytest.mark.offline
def test_spec_path_from_evidence_when_attr_absent(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_spec(tmp_path, _INVALID_SPEC)
    monkeypatch.setattr(implement_ready_spec, "_FILES_ROOT", tmp_path)
    _add_todo(conn)
    _add_assertion(conn, evidence_uris=_evidence_for(_INVALID_SPEC))
    findings = detect_implement_ready_spec_unvalidated(conn)
    assert len(findings) == 1
    assert "dense_spec_sections_missing" in findings[0]["detail"]


@pytest.mark.offline
def test_registered_fs_touching_warning() -> None:
    assert _KIND in FS_TOUCHING_KINDS
    assert SEVERITY[_KIND] == "warning"
    assert _KIND in get_all_detectors()
