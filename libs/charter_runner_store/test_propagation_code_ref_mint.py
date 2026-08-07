"""Mint-time code_ref validation and recovery (arc 6885)."""

from __future__ import annotations

import json
import subprocess

import pytest
from implement_admission.propagation_row import PropagationRow
from universal_workspace import get_workspace_root

from charter_runner_store.propagation_code_ref_mint import (
    REASON_UNSATISFIABLE_CODE_REF,
    UnresolvableCodeRefError,
    admit_error_for_unresolvable_code_ref,
    mint_row_with_resolved_code_ref,
    require_resolvable_code_ref,
    try_recover_code_ref,
)
from charter_runner_store.propagation_ledger import (
    list_open_rows,
    open_ledger_db,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import settle_open_row


def _head() -> str:
    root = get_workspace_root()
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ancestor() -> str:
    root = get_workspace_root()
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.real_git_resolve
def test_require_resolvable_rejects_working() -> None:
    with pytest.raises(UnresolvableCodeRefError, match="does not resolve") as exc:
        require_resolvable_code_ref("working", service="git_integration_worker")
    msg = str(exc.value)
    assert "working" in msg
    assert "Pass a resolvable git commit" in msg


@pytest.mark.real_git_resolve
def test_admit_error_message_is_actionable() -> None:
    err = admit_error_for_unresolvable_code_ref("working")
    assert err["reason"] == "code_ref_unresolvable"
    assert "working" in err["summary"]
    assert "HEAD" in err["fix_hint"]
    assert "40-char" in err["fix_hint"] or "short SHA" in err["fix_hint"]


@pytest.mark.real_git_resolve
def test_upsert_rejects_working_class_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    with pytest.raises(UnresolvableCodeRefError):
        upsert_open_rows(
            [
                PropagationRow(
                    service="git_integration_worker",
                    code_ref="working",
                    proof_class="process_live",
                )
            ]
        )
    assert list_open_rows() == []


@pytest.mark.real_git_resolve
def test_upsert_rejects_mangled_37_hex(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    mangled = "1812220dff49a0e0a5b1f36cad0bca1ad1fd8"
    assert len(mangled) == 37
    with pytest.raises(UnresolvableCodeRefError):
        upsert_open_rows(
            [
                PropagationRow(
                    service="git_integration_worker",
                    code_ref=mangled,
                    proof_class="process_live",
                )
            ]
        )


@pytest.mark.real_git_resolve
def test_mint_accepts_head_and_real_sha(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    head = _head()
    row = mint_row_with_resolved_code_ref(
        PropagationRow(
            service="git_integration_worker",
            code_ref="HEAD",
            proof_class="process_live",
        )
    )
    assert row.code_ref == head
    ids = upsert_open_rows([row])
    assert ids == [f"git_integration_worker:{head}:sync_restart"]


@pytest.mark.real_git_resolve
def test_try_recover_mangled_hex_prefix() -> None:
    mangled = "1812220dff49a0e0a5b1f36cad0bca1ad1fd8"
    recovered = try_recover_code_ref(mangled)
    assert recovered == "1812220dff49a0be4e0a7b1f36cad0bca1ad1fd8"


@pytest.mark.real_git_resolve
def test_stock_unresolvable_fails_with_recovered(tmp_path, monkeypatch) -> None:
    """Stock A-class: direct-SQL open row with mangled ref → fail, not close."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    mangled = "1812220dff49a0e0a5b1f36cad0bca1ad1fd8"
    recovered = "1812220dff49a0be4e0a7b1f36cad0bca1ad1fd8"
    db = open_ledger_db()
    try:
        db.execute(
            """
            INSERT INTO propagation_ledger (
              row_id, service, action, code_ref, safe_window, proof, proof_class,
              status, age_in_harvests, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, 1.0, 1.0)
            """,
            (
                f"git_integration_worker:{mangled}:sync_restart",
                "git_integration_worker",
                "sync_restart",
                mangled,
                "drain_required",
                "probe",
                "process_live",
            ),
        )
        db.commit()
    finally:
        db.close()
    row = list_open_rows()[0]
    head = _head()

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": head}

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "failed"
    assert list_open_rows() == []
    db = open_ledger_db()
    try:
        stored = db.execute(
            "SELECT status, proof_payload FROM propagation_ledger WHERE row_id=?",
            (row.row_id,),
        ).fetchone()
    finally:
        db.close()
    assert stored["status"] == "failed"
    payload = json.loads(stored["proof_payload"])
    assert payload["failure_reason"] == REASON_UNSATISFIABLE_CODE_REF
    assert payload["recovered_code_ref"] == recovered
    assert payload["status_claim_kind"] == "observed_of_attempt"


@pytest.mark.real_git_resolve
def test_valid_off_master_fails_deploy_line_not_code_ref(
    tmp_path, monkeypatch
) -> None:
    """Defect B: resolvable undeployed tip → unsatisfiable_deploy_line."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    off_master = "7206cba2022925049c1817df71d6a04b94418ca1"
    assert require_resolvable_code_ref(off_master) == off_master
    upsert_open_rows(
        [
            PropagationRow(
                service="git_integration_worker",
                code_ref=off_master,
                proof_class="process_live",
            )
        ]
    )
    row = list_open_rows()[0]
    head = _head()

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": head}

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "failed"
    assert "deploy-line" in result.detail
    db = open_ledger_db()
    try:
        stored = db.execute(
            "SELECT status, proof_payload FROM propagation_ledger WHERE row_id=?",
            (row.row_id,),
        ).fetchone()
    finally:
        db.close()
    payload = json.loads(stored["proof_payload"])
    assert stored["status"] == "failed"
    assert payload["failure_reason"] == "unsatisfiable_deploy_line"
    assert payload["failure_reason"] != REASON_UNSATISFIABLE_CODE_REF


@pytest.mark.real_git_resolve
def test_ancestry_satisfied_descendant_still_closes(tmp_path, monkeypatch) -> None:
    """Good path (auto-c90c9024edcb): descendant live retires ancestor — no fail."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    ancestor = _ancestor()
    head = _head()
    upsert_open_rows(
        [
            PropagationRow(
                service="git_integration_worker",
                code_ref=ancestor,
                proof_class="process_live",
            )
        ]
    )
    row = list_open_rows()[0]

    def probe(_service: str) -> dict[str, str]:
        return {"code_version": head}

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "closed"
    assert result.outcome != "failed"
    assert "ancestry satisfied" in result.detail
    assert list_open_rows() == []


def test_reopen_failed_row_removed() -> None:
    import charter_runner_store.propagation_ledger as ledger

    assert not hasattr(ledger, "reopen_failed_row")
