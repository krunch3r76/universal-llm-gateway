"""B→A async restart close gap — persist proof_before at submit, settle consumes it."""

from __future__ import annotations

from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import (
    list_open_rows,
    set_open_proof_payload,
    upsert_open_rows,
)
from charter_runner_store.propagation_terminal import settle_open_row
from charter_runner_store.propagation_terminal_persisted_before import (
    proof_before_payload_for_submit,
)

_SHA = "72d60f3b2f2522c1913f83dcaa2099f735ecff69"
_SHA_OTHER = "abc1230000000000000000000000000000000000"


def _mcp_client_visible_row(*, code_ref: str = _SHA) -> PropagationRow:
    return PropagationRow(
        service="mcp",
        code_ref=code_ref,
        safe_window="standalone_ok",
        proof="test probe",
        proof_class="client_visible",
    )


def _composite_payload(
    *,
    code_ref: str = _SHA,
    source_synced_at: str = "2026-08-11T13:25:17Z",
) -> dict[str, object]:
    return {
        "mcp_health": {
            "code_version": code_ref,
            "source_synced_at": source_synced_at,
        },
        "cortex_api": {"code_version": code_ref},
    }


def _persist_before(
    row_id: str,
    *,
    before: dict[str, object] | None,
    code_ref: str = _SHA,
) -> None:
    set_open_proof_payload(
        row_id,
        proof_payload=proof_before_payload_for_submit(
            before=before,
            after=_composite_payload(code_ref=code_ref, source_synced_at="2026-08-11T13:25:13Z"),
            code_ref=code_ref,
            manage_status="ok",
            proof_class_requested="client_visible",
            proof_class_executed="client_visible",
        ),
        defer_reason="proof_pending",
    )


def test_settle_closes_when_before_present_and_identity_changed(
    tmp_path, monkeypatch
) -> None:
    """Verdict 1: attestation changed ∧ proof_class satisfied ⇒ close."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    before = _composite_payload(
        code_ref=_SHA,
        source_synced_at="2026-08-11T13:25:13Z",
    )
    _persist_before(row.row_id, before=before)

    def probe(_service: str) -> dict[str, object]:
        return _composite_payload()

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_settle_stays_open_when_identity_changed_but_surfaces_unsatisfied(
    tmp_path, monkeypatch
) -> None:
    """Verdict 2: identity changed but proof_class predicate not satisfied ⇒ open."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    before = _composite_payload(source_synced_at="2026-08-11T13:25:13Z")
    _persist_before(row.row_id, before=before)

    def probe(_service: str) -> dict[str, object]:
        return {
            "mcp_health": {
                "code_version": _SHA,
                "source_synced_at": "2026-08-11T13:25:17Z",
            },
            "cortex_api": {"code_version": _SHA_OTHER},
        }

    result = settle_open_row(row, probe, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert result.outcome != "closed"
    assert len(list_open_rows()) == 1


def test_settle_stays_open_when_identity_unchanged(tmp_path, monkeypatch) -> None:
    """Verdict 3: attestation unchanged ⇒ stay open with identity-specific defer."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    same = _composite_payload()
    _persist_before(row.row_id, before=same)

    result = settle_open_row(row, lambda _s: same, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert "unchanged" in result.detail
    assert "probe carried no readable code_version" not in result.detail
    open_rows = list_open_rows()
    assert len(open_rows) == 1
    assert open_rows[0].defer_reason == "proof_identity_unchanged"


def test_settle_stays_open_when_identity_indeterminate(tmp_path, monkeypatch) -> None:
    """Verdict 4: attestation indeterminate ⇒ stay open; defer names oracle decline."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    before = {
        "mcp_health": {"code_version": _SHA, "pid": 1},
        "cortex_api": {"code_version": _SHA},
    }
    after = {
        "mcp_health": {"code_version": _SHA, "pid": 1},
        "cortex_api": {"code_version": _SHA},
    }
    _persist_before(row.row_id, before=before)

    result = settle_open_row(row, lambda _s: after, defer_if_unreachable=True)
    assert result.outcome == "deferred"
    assert "indeterminate" in result.detail
    assert list_open_rows()[0].defer_reason == "proof_identity_indeterminate"


def test_settle_legacy_row_without_before_stays_open(tmp_path, monkeypatch) -> None:
    """Verdict 5: absent/unreadable before ⇒ behave as today; do not close."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]

    result = settle_open_row(
        row,
        lambda _s: _composite_payload(),
        defer_if_unreachable=True,
    )
    assert result.outcome == "deferred"
    assert result.outcome != "closed"
    assert len(list_open_rows()) == 1


def test_settle_stale_before_code_ref_stays_open(tmp_path, monkeypatch) -> None:
    """Verdict 6: before for different code_ref ⇒ stay open and say so."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    set_open_proof_payload(
        row.row_id,
        proof_payload=proof_before_payload_for_submit(
            before=_composite_payload(code_ref=_SHA_OTHER),
            after=None,
            code_ref=_SHA_OTHER,
            manage_status="ok",
            proof_class_requested="client_visible",
            proof_class_executed="client_visible",
        ),
        defer_reason="proof_pending",
    )

    result = settle_open_row(
        row,
        lambda _s: _composite_payload(),
        defer_if_unreachable=True,
    )
    assert result.outcome == "deferred"
    assert "different code_ref" in result.detail
    assert list_open_rows()[0].defer_reason == "proof_stale_before_code_ref"


def test_settle_detail_names_missing_before_not_missing_code_version(
    tmp_path, monkeypatch
) -> None:
    """AC13: readable nested code_version but no before — detail must not blame code_version."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]

    result = settle_open_row(
        row,
        lambda _s: _composite_payload(),
        defer_if_unreachable=True,
    )
    assert result.outcome == "deferred"
    assert "probe carried no readable code_version" not in result.detail
    assert "proof_before absent" in result.detail


def test_malformed_persisted_before_treated_as_absent(tmp_path, monkeypatch) -> None:
    """Malformed non-dict proof_before does not close the row."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows([_mcp_client_visible_row()])
    row = list_open_rows()[0]
    set_open_proof_payload(
        row.row_id,
        proof_payload={
            "proof_before": "not-a-dict",
            "code_ref_at_submit": _SHA,
        },
        defer_reason="proof_pending",
    )

    result = settle_open_row(
        row,
        lambda _s: _composite_payload(),
        defer_if_unreachable=True,
    )
    assert result.outcome == "deferred"
    assert result.outcome != "closed"
    assert list_open_rows()[0].defer_reason == "proof_malformed_before"
