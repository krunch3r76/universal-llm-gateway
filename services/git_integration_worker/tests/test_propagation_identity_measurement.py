"""Arc 6655 — identity_measurement chokepoint at close_row."""

from __future__ import annotations

import json

import pytest
from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import (
    close_row,
    list_open_rows,
    open_ledger_db,
    set_open_proof_payload,
    upsert_open_rows,
)
from services.git_integration_worker.cursor_auto.propagation_probe import (
    IdentityMeasurementError,
)


@pytest.fixture(autouse=True)
def _synthetic_code_ref_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    from deploy_identity import code_ref_relation as relation_mod

    real = relation_mod.resolve_commit_sha

    def _fake(value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if len(normalized) == 40 and all(
            char in "0123456789abcdef" for char in normalized
        ):
            return normalized
        return real(value)

    monkeypatch.setattr(relation_mod, "resolve_commit_sha", _fake)
    monkeypatch.setattr(relation_mod, "_resolve_commit_sha", _fake)


def _row(**kwargs: object) -> PropagationRow:
    base: dict[str, object] = {
        "service": "git_integration_worker",
        "code_ref": "abc1230000000000000000000000000000000000",
        "proof_class": "process_live",
    }
    base.update(kwargs)
    return PropagationRow(**base)


def test_close_row_stamps_identity_measurement_measured(tmp_path, monkeypatch) -> None:
    """AC5 fail-first: closed payload lacked identity_measurement before chokepoint."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_row()])[0]
    close_row(
        row_id,
        proof_payload={
            "code_version": "abc1230000000000000000000000000000000000",
            "proof_class_requested": "process_live",
            "proof_class_executed": "process_live",
            "pid": 42,
        },
    )
    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT proof_payload FROM propagation_ledger WHERE row_id=?",
            (row_id,),
        )
        stored = cur.fetchone()
    finally:
        db.close()
    payload = json.loads(stored["proof_payload"])
    assert payload["identity_measurement"] == "measured"


def test_close_row_stamps_ancestry_identity_measurement_absent(
    tmp_path, monkeypatch
) -> None:
    """AC4: ancestry closes stamp absent without breaking close."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    row_id = upsert_open_rows([_row()])[0]
    close_row(
        row_id,
        proof_payload={
            "code_version": "def4560000000000000000000000000000000000",
            "version_satisfaction_case": "ancestry_satisfied",
            "code_ref_relation": "ancestor",
        },
    )
    db = open_ledger_db()
    try:
        cur = db.execute(
            "SELECT proof_payload FROM propagation_ledger WHERE row_id=?",
            (row_id,),
        )
        stored = cur.fetchone()
    finally:
        db.close()
    payload = json.loads(stored["proof_payload"])
    assert payload["identity_measurement"] == "absent"
    assert "identity_attestation" not in payload


def test_close_row_malformed_before_does_not_close(tmp_path, monkeypatch) -> None:
    """AC7 fail-first: malformed persisted before must not close silently."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    sha = "abc1230000000000000000000000000000000000"
    row_id = upsert_open_rows([_row(code_ref=sha)])[0]
    set_open_proof_payload(
        row_id,
        proof_payload={
            "proof_before": "not-a-dict",
            "code_ref_at_submit": sha,
        },
    )
    with pytest.raises(IdentityMeasurementError, match="not a dict"):
        close_row(
            row_id,
            proof_payload={
                "code_version": sha,
                "version_satisfaction_case": "exact_match",
            },
        )
    assert len(list_open_rows()) == 1
