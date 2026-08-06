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


def test_unrelated_observation_defers_with_observed_version_payload(
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
    assert result.outcome == "deferred"
    assert result.code_ref == intent
    assert observed in result.detail
    still_open = list_open_rows()
    assert len(still_open) == 1
    assert still_open[0].code_ref == intent

    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT code_ref, status, proof_payload FROM propagation_ledger WHERE row_id=?",
            (row.row_id,),
        )
        stored = cur.fetchone()
    finally:
        db.close()
    assert stored["status"] == "open"
    assert stored["code_ref"] == intent
    payload = json.loads(stored["proof_payload"])
    assert payload["observed_code_version"] == observed
