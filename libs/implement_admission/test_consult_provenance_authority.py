"""Fail-before / pass-after tests for the O3 consult-provenance authority bind.

AC4: both gate codes must fail on today's attr-trusting evaluator and pass
after the record becomes SoT. Attrs present without a record must not admit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import evaluate_implement_ready

_NOW = "2026-08-14T00:00:00+00:00"
_TODO = "todo:consult-provenance-authority-bind"
_SPEC = "tasks/specs/consult-provenance-authority-bind.md"
_ARCHIVE_BODY = b"consult archive body for sha probe\n"

_VALID_DENSE_SPEC = """\
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


def _ready_kwargs(**overrides: object) -> dict:
    base: dict[str, object] = {
        "todo_id": _TODO,
        "density_triage": "judgment_required",
        "source_uri": _SPEC,
        "implement_ready_assertion_id": 42,
        "assertion": {
            "entity_id": _TODO,
            "superseded_by": None,
            "valid_until": None,
            "evidence_uris": [
                f"workspaces://universal-llm-gateway/{_SPEC}",
                dense_spec_hash_uri(_VALID_DENSE_SPEC),
            ],
        },
        "now_iso": _NOW,
        "dense_spec_uri": f"workspaces://universal-llm-gateway/{_SPEC}",
        "dense_spec_text": _VALID_DENSE_SPEC,
        "files_expected": ["module.py"],
        "acceptance_criteria": ["Validator passes dense specs."],
        "entity_name": "consult-provenance-authority-bind",
    }
    base.update(overrides)
    return base


def _archive_pair(root: Path) -> tuple[str, str]:
    rel = Path("notes/system/threads/archives/authority-bind.md")
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_ARCHIVE_BODY)
    uri = f"cortex://{rel.as_posix()}"
    sha = hashlib.sha256(_ARCHIVE_BODY).hexdigest()
    return uri, sha


def _complete_record(root: Path, **overrides: object) -> dict[str, object]:
    uri, sha = _archive_pair(root)
    payload: dict[str, object] = {
        "todo": _TODO,
        "consult_thread": "agent-bus:8801#12",
        "verdict": "ADMIT",
        "adjudication_assertion_id": 29377,
        "consultant_model": "claude-fable-5-1",
        "consultant_effort": "high",
        "consultant_substrate": "cdp",
        "archive_uri": uri,
        "archive_sha256": sha,
        "satellite_execution_id": "sat-1",
        "stargate_execution_id": "sg-1",
        "written_by": "implement_admission.commit_todo_consult_provenance",
        "written_at": "2026-08-14T00:00:00Z",
    }
    payload.update(overrides)
    return payload


@pytest.mark.offline
def test_attrs_without_record_yields_missing() -> None:
    """Hand-stamped attrs must not admit — missing record keeps the old code."""
    verdict = evaluate_implement_ready(**_ready_kwargs())
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_missing"


@pytest.mark.offline
def test_incomplete_record_yields_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Present-but-incomplete record is the new unverifiable code."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path)
    del record["adjudication_assertion_id"]
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_unverifiable"


@pytest.mark.offline
def test_sha_mismatch_yields_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path, archive_sha256="0" * 64)
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_unverifiable"


@pytest.mark.offline
def test_record_effort_key_absent_is_unverifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path)
    del record["consultant_effort"]
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_unverifiable"
    assert "consultant_effort" in (verdict.reason or "")


@pytest.mark.offline
def test_record_effort_null_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path, consultant_effort=None)
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is True


@pytest.mark.offline
def test_record_effort_enum_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path, consultant_effort="turbo")
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_unverifiable"


@pytest.mark.offline
def test_record_model_unknown_or_unfolded_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    unknown = _complete_record(tmp_path, consultant_model="unknown")
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=unknown)
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_unverifiable"

    unfolded = _complete_record(tmp_path, consultant_model="cdp/fable")
    verdict2 = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=unfolded)
    )
    assert verdict2.admitted is False
    assert verdict2.code == "implement_consult_provenance_unverifiable"


@pytest.mark.offline
def test_complete_record_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path)
    verdict = evaluate_implement_ready(
        **_ready_kwargs(consult_provenance_record=record)
    )
    assert verdict.admitted is True
    assert verdict.assertion_id == 42


@pytest.mark.offline
def test_commit_is_sole_todo_keyed_writer() -> None:
    from implement_admission.consult_provenance_record import (
        TODO_CONSULT_PROVENANCE_DIR,
        commit_todo_consult_provenance,
    )

    root = Path(__file__).resolve().parents[2]
    writers: list[str] = []
    scan_roots = (root / "libs" / "implement_admission", root / "scripts" / "model_manager")
    for scan in scan_roots:
        for path in scan.rglob("*.py"):
            if "test_" in path.name or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if TODO_CONSULT_PROVENANCE_DIR in text and "write_text" in text:
                writers.append(str(path.relative_to(root)))
    assert writers == [
        "libs/implement_admission/consult_provenance_record.py"
    ]
    assert commit_todo_consult_provenance.__name__ == "commit_todo_consult_provenance"


@pytest.mark.offline
def test_commit_refuses_incomplete_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from implement_admission.consult_provenance_record import (
        commit_todo_consult_provenance,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path)
    del record["adjudication_assertion_id"]
    assert commit_todo_consult_provenance(record, files_root=tmp_path) is None
    record2 = _complete_record(tmp_path)
    del record2["consultant_model"]
    assert commit_todo_consult_provenance(record2, files_root=tmp_path) is None


@pytest.mark.offline
def test_commit_writes_record_and_builds_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from implement_admission.consult_provenance_record import (
        commit_todo_consult_provenance,
        load_todo_consult_provenance,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    record = _complete_record(tmp_path)
    uri = commit_todo_consult_provenance(
        record, stamp_cache=False, files_root=tmp_path
    )
    assert uri is not None
    loaded = load_todo_consult_provenance(_TODO, root=tmp_path)
    assert loaded is not None
    assert loaded["todo"] == _TODO
    assert loaded["adjudication_assertion_id"] == 29377
    assert loaded["consultant_effort"] == "high"
    assert "consultant_family" not in loaded


@pytest.mark.offline
def test_not_ready_still_fires_before_provenance() -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage="judgment_required",
        source_uri=_SPEC,
        implement_ready_assertion_id=None,
        assertion=None,
        now_iso=_NOW,
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_not_ready_judgment_required"
