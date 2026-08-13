"""Unit tests for propagation ledger mint honesty (row 17 bind B)."""

from __future__ import annotations

import json

import pytest
from implement_admission.propagation_row import PropagationRow, default_proof

from charter_runner_store.propagation_ledger import (
    PerformedAncestryProofError,
    fail_row,
    list_open_rows,
    open_ledger_db,
    scoreboard_projection,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import settle_open_row


def _row(**kwargs: object) -> PropagationRow:
    base: dict[str, object] = {
        "service": "git_integration_worker",
        "code_ref": "abc1230000000000000000000000000000000000",
        "proof_class": "process_live",
    }
    base.update(kwargs)
    return PropagationRow(**base)


def test_upsert_open_row_preserves_intent_and_obligation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    intent = "abc1230000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=intent)])
    open_rows = list_open_rows()
    assert len(open_rows) == 1
    assert open_rows[0].code_ref == intent
    assert open_rows[0].proof_kind == "obligation"
    assert "AFTER restart VERIFY" in open_rows[0].proof
    assert "ancestry satisfied" not in open_rows[0].proof.lower()

    with pytest.raises(PerformedAncestryProofError):
        upsert_open_rows(
            [
                _row(
                    code_ref="def4560000000000000000000000000000000000",
                    proof="liveness code_ref ancestry satisfied",
                )
            ]
        )


def test_open_projection_marks_proof_obligation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(service="mcp", proof_class="client_visible")])
    board = scoreboard_projection()
    assert len(board) == 1
    assert board[0]["proof_kind"] == "obligation"
    assert board[0]["proof"] == default_proof("mcp", "client_visible")
    assert "ancestry satisfied" not in board[0]["proof"].lower()


def test_stale_observation_fails_without_rewriting_intent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    intent = "abc1230000000000000000000000000000000000"
    observed = "861a623700000000000000000000000000000000"
    row_id = upsert_open_rows([_row(code_ref=intent)])[0]
    fail_row(
        row_id,
        proof_payload={
            "expected_code_ref": intent,
            "observed_code_version": observed,
            "code_version": observed,
        },
        reason="code_version_mismatch",
    )
    assert list_open_rows() == []
    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT code_ref, status, proof_payload FROM propagation_ledger WHERE row_id=?",
            (row_id,),
        )
        stored = cur.fetchone()
    finally:
        db.close()
    assert stored["code_ref"] == intent
    assert stored["status"] == "failed"
    payload = json.loads(stored["proof_payload"])
    assert payload["observed_code_version"] == observed


def test_unrelated_observation_fails_deploy_line_without_rewriting_intent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    intent = "abc1230000000000000000000000000000000000"
    observed = "ffff000000000000000000000000000000000000"
    upsert_open_rows([_row(code_ref=intent)])
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": observed}

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "failed"
    assert result.code_ref == intent
    assert list_open_rows() == []

    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT code_ref, status, proof_payload FROM propagation_ledger WHERE row_id=?",
            (row.row_id,),
        )
        stored = cur.fetchone()
    finally:
        db.close()
    assert stored["status"] == "failed"
    assert stored["code_ref"] == intent
    payload = json.loads(stored["proof_payload"])
    assert payload["observed_code_version"] == observed
    assert payload["failure_reason"] == "unsatisfiable_deploy_line"
    assert payload["status_claim_kind"] == "observed_of_attempt"


def test_upsert_persists_allow_self_preempt_and_force(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows(
        [
            _row(
                service="mcp",
                allow_self_preempt=False,
                force=True,
            )
        ]
    )
    open_rows = list_open_rows()
    assert len(open_rows) == 1
    assert open_rows[0].allow_self_preempt is False
    assert open_rows[0].force is True


def test_upsert_omitted_flags_use_model_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_row(service="mcp")])
    open_rows = list_open_rows()
    assert len(open_rows) == 1
    assert open_rows[0].allow_self_preempt is True
    assert open_rows[0].force is False


def test_projection_to_row_round_trips_force_flags(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        _projection_to_row,
    )

    upsert_open_rows([_row(service="mcp", allow_self_preempt=False, force=True)])
    projection = list_open_rows()[0]
    rebuilt = _projection_to_row(projection)
    assert rebuilt.allow_self_preempt is False
    assert rebuilt.force is True
