"""Skeptic waiver override tests for implement admission distillation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store import db
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.dispatch_ops import execute_op

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

    conn_ctx = db.cortex_conn()
    conn = conn_ctx.__enter__()
    try:
        now = "2026-06-15T00:00:00Z"
        conn.execute(
            "INSERT INTO entities "
            "(id, type, name, workflow_state, attributes, created_at, updated_at) "
            "VALUES (?, 'todo', ?, 'in_progress', ?, ?, ?)",
            (
                "todo:wire-gate",
                "wire-gate",
                json.dumps({"density_triage": "judgment_required"}),
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


@pytest.mark.offline
def test_waiver_admits_without_skeptic_and_writes_structured_attr(
    gate_env: Path,
) -> None:
    with patch(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.cortex_implement_recon_waived"
    ) as emit:
        result = _distill(
            recon_waive_reason_code="ratified_on_prior_spec_revision",
            recon_waive_reason="spec hash changed after skeptic",
        )

    assert result.get("ok") is True, result
    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    raw = entity["attributes"]["recon_waived"]
    parsed = json.loads(raw)
    assert parsed["reason_code"] == "ratified_on_prior_spec_revision"
    assert parsed["reason"] == "spec hash changed after skeptic"
    assert parsed["waived_by"] == "claude-cursor"
    assert parsed["spec_sha256"].startswith("spec_sha256:")
    emit.assert_called_once()
    payload = emit.call_args.kwargs
    assert payload["reason_code"] == "ratified_on_prior_spec_revision"
    assert payload["spec_sha256"] == parsed["spec_sha256"]


@pytest.mark.offline
def test_unknown_reason_code_rejected_before_write(gate_env: Path) -> None:
    with patch(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.cortex_implement_recon_waived"
    ) as emit:
        result = _distill(recon_waive_reason_code="bogus")

    assert result.get("code") == "recon_waive_reason_code_unknown"
    assert "error" in result
    emit.assert_not_called()
    with db.cortex_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert count == 0


@pytest.mark.offline
def test_same_waiver_redistill_is_idempotent_no_duplicate_event(
    gate_env: Path,
) -> None:
    waiver_kwargs = {
        "recon_waive_reason_code": "operator_directive",
        "recon_waive_reason": "operator approved",
    }
    with patch(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.cortex_implement_recon_waived"
    ) as emit:
        first = _distill(**waiver_kwargs)
        assert first.get("ok") is True, first
        emit.assert_called_once()

        second = _distill(**waiver_kwargs)
        assert second.get("ok") is True, second
        assert second.get("idempotent") is True
        emit.assert_called_once()


@pytest.mark.offline
def test_changed_waiver_redistill_persists_and_emits_again(gate_env: Path) -> None:
    with patch(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.cortex_implement_recon_waived"
    ) as emit:
        first = _distill(
            recon_waive_reason_code="operator_directive",
            recon_waive_reason="first reason",
        )
        assert first.get("ok") is True, first

        second = _distill(
            recon_waive_reason_code="operator_directive",
            recon_waive_reason="changed reason",
        )
        assert second.get("ok") is True, second
        assert second.get("idempotent") is not True

    assert emit.call_count == 2
    entity = execute_op("entity_get", {"entity_id": "todo:wire-gate", "intent": "full"})
    parsed = json.loads(entity["attributes"]["recon_waived"])
    assert parsed["reason"] == "changed reason"


@pytest.mark.offline
def test_without_waiver_still_skeptic_pass_missing(gate_env: Path) -> None:
    result = _distill()
    assert result.get("ok") is not True, result
    assert result.get("gate_code") == "skeptic_pass_missing"
