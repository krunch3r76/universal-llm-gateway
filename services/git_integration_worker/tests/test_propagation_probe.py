"""Unit tests for propagation_probe proof_class closure (a:27414 fix b)."""

from __future__ import annotations

from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.propagation_probe import (
    proof_observed,
)


def _row(
    service: str,
    code_ref: str,
    *,
    proof_class: str = "process_live",
) -> PropagationRow:
    return PropagationRow(
        service=service,
        code_ref=code_ref,
        safe_window="standalone_ok",
        proof="test probe",
        proof_class=proof_class,
    )


def test_proof_observed_process_live_match() -> None:
    row = _row("mcp", "abc123", proof_class="process_live")
    assert proof_observed(row, {"code_version": "abc123"}) is True


def test_proof_observed_process_live_mismatch() -> None:
    row = _row("mcp", "abc123", proof_class="process_live")
    assert proof_observed(row, {"code_version": "deadbeef"}) is False


def test_client_visible_mcp_requires_both_surfaces() -> None:
    row = _row("mcp", "abc123", proof_class="client_visible")
    both_match = {
        "mcp_health": {"code_version": "abc123"},
        "cortex_api": {"code_version": "abc123"},
    }
    assert proof_observed(row, both_match) is True


def test_client_visible_mcp_instance2_replay_mcp_only_match() -> None:
    """agent-bus:6608 INSTANCE 2 — mcp health match alone must not close."""
    row = _row("mcp", "abc123", proof_class="client_visible")
    mcp_only = {
        "mcp_health": {"code_version": "abc123"},
        "cortex_api": {"code_version": "stale000"},
    }
    assert proof_observed(row, mcp_only) is False


def test_client_visible_mcp_missing_cortex_api() -> None:
    row = _row("mcp", "abc123", proof_class="client_visible")
    assert proof_observed(
        row,
        {"mcp_health": {"code_version": "abc123"}, "cortex_api": None},
    ) is False


def test_client_visible_mcp_flat_payload_rejected() -> None:
    row = _row("mcp", "abc123", proof_class="client_visible")
    assert proof_observed(row, {"code_version": "abc123"}) is False
