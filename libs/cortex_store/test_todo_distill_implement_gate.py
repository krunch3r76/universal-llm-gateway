"""Integration tests for todo_distill_implement_gate dispatch op."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.gate_distillation import prepare_gate_distillation

from cortex_store import db
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops._todo_gate_distillation_impl import (
    _evaluate_from_persisted,
)
from cortex_store.type_schemas import type_attribute_schema

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

- libs/a.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Wire the gate trio.

## 7. Acceptance criteria

1. Admission passes without manual fixup.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""

_GATE_KWARGS = {
    "files_expected": ["libs/a.py"],
    "acceptance_criteria": ["Admission passes without manual fixup."],
    "agent": "claude-cursor",
}
_SPEC_URI = "workspaces://universal-llm-gateway/tasks/specs/wire-gate.md"


@pytest.fixture()
def gate_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    migrated_db_template: Path,
) -> Path:
    spec_dir = tmp_path / "universal-llm-gateway" / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    (spec_dir / "wire-gate.md").write_text(_VALID_DENSE_SPEC, encoding="utf-8")

    db_path = tmp_path / "cortex.db"
    copy_template_db(migrated_db_template, db_path)
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    files_root = tmp_path / "cortex-files"
    files_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files_root))
    archive_rel = Path("notes/system/threads/archives/wire-gate.md")
    archive_path = files_root / archive_rel
    archive_path.parent.mkdir(parents=True)
    archive_body = b"wire-gate consult archive\n"
    archive_path.write_bytes(archive_body)
    record = {
        "todo": "todo:wire-gate",
        "consult_thread": "agent-bus:8801#12",
        "verdict": "ADMIT",
        "adjudication_assertion_id": 1,
        "consultant_model": "claude-fable-5-1",
        "consultant_effort": "high",
        "consultant_substrate": "cdp",
        "archive_uri": f"cortex://{archive_rel.as_posix()}",
        "archive_sha256": hashlib.sha256(archive_body).hexdigest(),
        "satellite_execution_id": "sat-1",
        "stargate_execution_id": "sg-1",
        "written_by": "test",
        "written_at": "2026-06-15T00:00:00Z",
    }
    rec_dir = files_root / "notes/system/threads/todo-consult-provenance"
    rec_dir.mkdir(parents=True)
    (rec_dir / "wire-gate.json").write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )

    conn_ctx = db.cortex_conn()
    conn = conn_ctx.__enter__()
    try:
        now = "2026-06-15T00:00:00Z"
        conn.execute(
            "INSERT INTO entities "
            "(id, type, name, source_uri, workflow_state, attributes, created_at, updated_at) "
            "VALUES (?, 'todo', ?, ?, 'in_progress', ?, ?, ?)",
            (
                "todo:wire-gate",
                "wire-gate",
                _SPEC_URI,
                json.dumps(
                    {
                        "density_triage": "judgment_required",
                        "check_requested": True,
                        "consult_thread": "agent-bus:8801",
                        "verdict": "proceed_with_amendments",
                        "consultant_model": "claude-fable-5-1",
        "consultant_effort": "high",
                        "consultant_substrate": "web-anthropic",
                    }
                ),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn_ctx.__exit__(None, None, None)

    return tmp_path


def _distill(**overrides: object) -> dict:
    payload = {"todo_id": "todo:wire-gate", **_GATE_KWARGS, **overrides}
    return execute_op("todo_distill_implement_gate", payload)


def _seed_skeptic_ratification(spec_text: str = _VALID_DENSE_SPEC) -> None:
    execute_op(
        "assert",
        {
            "entity_id": "todo:wire-gate",
            "claim": "status(todo:wire-gate, skeptic_ratified, current)",
            "confidence": "confirmed",
            "evidence": "Skeptic ratified for gate test",
            "derivation_type": "agent_observation",
            "predicate_form": "status(todo:wire-gate, skeptic_ratified, current)",
            "evidence_uris": [dense_spec_hash_uri(spec_text)],
            "seeded_by": "test_todo_distill_implement_gate",
        },
    )


def _evaluate_from_persisted_state() -> object:
    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    prepared = prepare_gate_distillation(
        todo_id="todo:wire-gate",
        source_uri=entity.get("source_uri"),
    )
    assert not isinstance(prepared, tuple)
    return _evaluate_from_persisted(
        entity_id="todo:wire-gate",
        prepared=prepared,
    )


@pytest.mark.offline
def test_migrated_todo_schema_registers_implement_lane_keys(
    gate_env: Path,
) -> None:
    with db.cortex_conn() as conn:
        schema = type_attribute_schema(conn, "todo")
    assert schema is not None
    optional = schema["optional"]
    assert isinstance(optional, list)
    for key in ("files_expected", "acceptance_criteria", "required_skills"):
        assert key in optional


@pytest.mark.offline
def test_todo_distill_implement_gate_wires_trio(gate_env: Path) -> None:
    _seed_skeptic_ratification()
    result = _distill()
    assert result.get("ok") is True, result
    assert result["source_uri"] == _SPEC_URI
    assert result["implement_ready_assertion_id"] == 2
    assert result["evidence_uris"][0] == _SPEC_URI
    assert result["evidence_uris"][1].startswith("spec_sha256:")

    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    assert entity["source_uri"] == _SPEC_URI
    attrs = entity["attributes"]
    assert attrs["implement_ready_assertion_id"] == 2
    assert attrs["files_expected"] == ["libs/a.py"]
    assert attrs["acceptance_criteria"] == ["Admission passes without manual fixup."]
    assert attrs["bind_status"] == "settled"
    assert attrs["next_action"] == "run_address_or_ship"
    assert attrs["workflow"] == "path_sim"

    assertion = execute_op("assertion_get", {"assertion_id": 2})
    assert assertion["entity_id"] == "todo:wire-gate"
    assert _SPEC_URI in assertion["evidence_uris"]


@pytest.mark.offline
def test_todo_distill_passes_evaluate_from_persisted_state(gate_env: Path) -> None:
    _seed_skeptic_ratification()
    result = _distill()
    assert result.get("ok") is True, result

    verdict = _evaluate_from_persisted_state()
    assert verdict.admitted is True
    assert verdict.assertion_id == result["implement_ready_assertion_id"]


@pytest.mark.offline
def test_todo_distill_implement_gate_idempotent_rerun(gate_env: Path) -> None:
    _seed_skeptic_ratification()
    first = _distill()
    assert first.get("ok") is True, first

    second = _distill()
    assert second.get("ok") is True, second
    assert second.get("idempotent") is True
    assert (
        second["implement_ready_assertion_id"] == first["implement_ready_assertion_id"]
    )

    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    assert entity["attributes"]["bind_status"] == "settled"

    with db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 2


@pytest.mark.offline
def test_todo_distill_entity_update_failure_retracts_assertion(
    gate_env: Path,
) -> None:
    _seed_skeptic_ratification()
    with patch(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_update",
        return_value={"error": "simulated entity_update failure"},
    ):
        result = _distill()

    assert result.get("ok") is not True
    assert result.get("step") == "entity_update"
    assert "status" not in result
    assert "simulated entity_update failure" in result["error"]

    with db.cortex_conn() as conn:
        assertion_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
        row = conn.execute("SELECT valid_until FROM assertions WHERE id = 2").fetchone()
    assert assertion_count == 2
    assert row is not None
    assert row["valid_until"] is not None

    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    assert entity["attributes"].get("implement_ready_assertion_id") is None


@pytest.mark.offline
def test_todo_distill_implement_gate_rejects_empty_files(gate_env: Path) -> None:
    result = execute_op(
        "todo_distill_implement_gate",
        {
            "todo_id": "todo:wire-gate",
            "files_expected": [],
            "acceptance_criteria": ["AC"],
        },
    )
    assert "error" in result


@pytest.mark.offline
def test_todo_distill_post_check_rejects_missing_skeptic(gate_env: Path) -> None:
    result = _distill()
    assert result.get("ok") is not True, result
    assert result.get("gate_code") == "skeptic_pass_missing"

    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    attrs = entity["attributes"]
    assert attrs.get("implement_ready_assertion_id") is None
    assert attrs.get("bind_status") != "settled"

    with db.cortex_conn() as conn:
        row = conn.execute(
            "SELECT valid_until FROM assertions WHERE id = 1",
        ).fetchone()
        assertion_count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert assertion_count == 1
    assert row is not None
    assert row["valid_until"] is not None


@pytest.mark.offline
def test_todo_distill_post_check_passes_with_skeptic_ratification(
    gate_env: Path,
) -> None:
    _seed_skeptic_ratification()
    result = _distill()
    assert result.get("ok") is True, result

    verdict = _evaluate_from_persisted_state()
    assert verdict.admitted is True
    assert verdict.code is None


@pytest.mark.offline
def test_todo_distill_implement_gate_mechanical_bypasses_skeptic(
    gate_env: Path,
) -> None:
    result = _distill(density_triage="mechanical")
    assert result.get("ok") is True, result
    assertion_id = result["implement_ready_assertion_id"]
    assert isinstance(assertion_id, int)

    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    assert entity["attributes"]["density_triage"] == "mechanical"
    assert entity["attributes"]["implement_ready_assertion_id"] == assertion_id
    assert entity["attributes"]["files_expected"] == ["libs/a.py"]

    verdict = _evaluate_from_persisted_state()
    assert verdict.admitted is True


@pytest.mark.offline
def test_todo_distill_implement_gate_rejects_recon_pending(gate_env: Path) -> None:
    result = _distill(density_triage="recon_pending")
    assert result.get("ok") is not True
    assert result.get("code") == "implement_blocked_recon_pending"
