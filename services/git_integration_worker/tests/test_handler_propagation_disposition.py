"""Unit tests for propagate envelope disposition derived from executions[]."""

from __future__ import annotations

from unittest.mock import patch

from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.handler_propagation import (
    _disposition_for,
    _summary_for,
    execution_for_manage_deferred,
    restart_intent_persisted,
)


def _exec(
    service: str,
    status: str,
    *,
    reason: str | None = None,
    manage: dict | None = None,
) -> dict:
    row: dict = {"service": service, "status": status}
    if reason is not None:
        row["reason"] = reason
    if manage is not None:
        row["manage"] = manage
    return row


# --- AC1: manage status=error must not yield propagated/executed ---


def test_disposition_manage_error_not_propagated() -> None:
    executions = [
        _exec(
            "cortex-api",
            "failed",
            manage={
                "status": "error",
                "reason": "manage_rpc_error",
                "error": "Unknown service: 'cortex-api'",
            },
        ),
    ]
    disposition = _disposition_for(executions)
    assert disposition not in ("propagated", "executed")
    assert disposition == "failed"


def test_disposition_manage_error_row_status_submitted_still_failed() -> None:
    """Manage error floors even when row status is optimistic."""
    executions = [
        _exec(
            "mcp",
            "submitted",
            manage={"status": "error", "reason": "manage_rpc_error"},
        ),
    ]
    assert _disposition_for(executions) == "failed"


# --- AC2: weakest row floors mixed sets ---


def test_disposition_mixed_executed_submitted_queued_floors_to_queued() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
        _exec("stargate", "queued", reason="draining"),
    ]
    assert _disposition_for(executions) == "queued"


def test_disposition_mixed_executed_and_submitted_floors_to_submitted() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
    ]
    assert _disposition_for(executions) == "submitted"


def test_disposition_mixed_executed_and_failed_floors_to_failed() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_any_failed_row_never_propagated() -> None:
    """Failed-axis: no execution set containing failed may yield propagated."""
    failed_variants = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec("cortex-api", "failed", manage={"status": "error", "reason": "manage_rpc_error"}),
        _exec("gateway", "failed", reason="proof_class_unsupported"),
        _exec("stargate", "submitted", manage={"status": "error", "reason": "manage_rpc_error"}),
        _exec("rag", "executed", manage={"status": "error", "reason": "manage_error"}),
        _exec("mcp", "unknown_status_not_in_map"),
    ]
    ok_variants = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
        _exec("stargate", "queued", reason="draining"),
        _exec("git_integration_worker", "blocked", reason="busy"),
    ]
    for failed in failed_variants:
        for ok in ok_variants:
            disposition = _disposition_for([ok, failed])
            assert disposition != "propagated", (
                f"failed={failed!r} mixed with ok={ok!r} yielded propagated"
            )
        assert _disposition_for([failed]) != "propagated"
        assert _disposition_for([failed, failed]) != "propagated"


# --- uniform sets ---


def test_disposition_all_failed_returns_failed() -> None:
    executions = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec("git_integration_worker", "failed", manage={"reason": "not running"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_empty_executions_returns_failed() -> None:
    assert _disposition_for([]) == "failed"


def test_disposition_all_executed_maps_to_propagated() -> None:
    assert _disposition_for([_exec("mcp", "executed")]) == "propagated"


def test_disposition_all_submitted_returns_submitted() -> None:
    assert _disposition_for([_exec("mcp", "submitted")]) == "submitted"


def test_disposition_submitted_never_propagated_while_proof_pending() -> None:
    """AC-1: open-row (submitted) executions must not yield propagated envelope."""
    executions = [
        _exec(
            "mcp",
            "submitted",
            manage={"status": "ok", "message": "restart scheduled"},
        ),
    ]
    disposition = _disposition_for(executions)
    assert disposition == "submitted"
    assert disposition != "propagated"


def test_disposition_all_queued_unchanged() -> None:
    assert _disposition_for([_exec("mcp", "queued", reason="draining")]) == "queued"


def test_disposition_all_blocked_returns_blocked() -> None:
    assert _disposition_for([_exec("mcp", "blocked", reason="busy")]) == "blocked"


def test_disposition_mixed_executed_and_blocked_floors_to_blocked() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "blocked", reason="busy"),
    ]
    assert _disposition_for(executions) == "blocked"


def test_restart_intent_persisted_requires_intent_id() -> None:
    assert restart_intent_persisted({"status": "deferred", "state": "draining"}) is False
    assert restart_intent_persisted({"status": "deferred", "restart_intent_id": "x"}) is True


def test_execution_for_manage_deferred_without_intent_is_blocked() -> None:
    row = PropagationRow(
        service="mcp",
        code_ref="deadbeef",
        action="sync_restart",
        proof_class="client_visible",
    )
    with patch(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
    ) as mock_set:
        result = execution_for_manage_deferred(
            row,
            row_id="mcp:deadbeef:sync_restart",
            manage_result={
                "status": "deferred",
                "state": "busy",
                "reason": "cdp_ask_live",
            },
        )
    assert result["status"] == "blocked"
    assert "nothing will fire" in result["next"].lower()
    mock_set.assert_called_once_with("mcp:deadbeef:sync_restart", "manage_busy_defer")


def test_execution_for_manage_deferred_with_intent_is_queued() -> None:
    row = PropagationRow(
        service="git_integration_worker",
        code_ref="deadbeef",
        action="sync_restart",
        proof_class="process_live",
    )
    with patch(
        "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
    ) as mock_set:
        result = execution_for_manage_deferred(
            row,
            row_id="git_integration_worker:deadbeef:sync_restart",
            manage_result={
                "status": "deferred",
                "state": "draining",
                "restart_intent_id": "intent-abc",
                "reason": "draining; completion delivered via git_worker.drain events",
            },
        )
    assert result["status"] == "queued"
    assert "will fire" in result["next"].lower()
    mock_set.assert_called_once_with(
        "git_integration_worker:deadbeef:sync_restart", "manage_queued_drain"
    )


def test_summary_blocked_does_not_claim_will_fire() -> None:
    executions = [_exec("mcp", "blocked", reason="cdp_ask_live")]
    summary = _summary_for("blocked", executions)
    assert "nothing will fire" in summary.lower()
    assert "will fire after drain" not in summary.lower()


# --- AC3: D7/turn-27 payload replay ---


def test_disposition_d7_turn27_payload_replay() -> None:
    executions = [
        {
            "service": "cortex-api",
            "row_id": "cortex-api:6ab3a8165e0d3bef418adeeb5a7622b666ba8664:sync_restart",
            "status": "failed",
            "manage": {
                "status": "error",
                "reason": "manage_rpc_error",
                "error": (
                    "Unknown service: 'cortex-api'. Valid: agent_bus, cdp_ask, "
                    "cloud_proxy, cortex_api, email_bridge, event_service, gateway, "
                    "git_integration_worker, mcp, rag, stargate"
                ),
            },
        }
    ]
    assert _disposition_for(executions) == "failed"


# --- summary honesty ---


def test_summary_all_failed_names_services_and_reasons() -> None:
    executions = [_exec("mcp", "failed", manage={"reason": "socket refused"})]
    summary = _summary_for("failed", executions)
    assert "mcp" in summary
    assert "socket refused" in summary
    assert "submitted or queued" not in summary.lower()


def test_summary_mixed_executed_and_failed_surfaces_partial_and_failure() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    summary = _summary_for("failed", executions)
    assert "git_integration_worker" in summary
    assert "manage_error" in summary
    assert "failed" in summary.lower()
    assert "partial progress" in summary.lower()


def test_summary_submitted_names_open_ledger() -> None:
    executions = [_exec("mcp", "submitted")]
    summary = _summary_for("submitted", executions)
    assert "submitted" in summary.lower()
    assert "ledger row open" in summary.lower()


def test_summary_propagated_claims_proof_observed() -> None:
    executions = [_exec("mcp", "executed")]
    summary = _summary_for("propagated", executions)
    assert "proof-of-live observed" in summary.lower()
