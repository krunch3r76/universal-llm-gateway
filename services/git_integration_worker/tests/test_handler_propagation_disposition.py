"""Unit tests for propagate envelope disposition derived from executions[]."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.handler_propagation import (
    _disposition_for,
    _summary_for,
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


def test_disposition_mixed_executed_and_submitted_floors_to_propagated() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
    ]
    assert _disposition_for(executions) == "propagated"


def test_disposition_mixed_executed_and_failed_floors_to_failed() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    assert _disposition_for(executions) == "failed"


# --- uniform sets ---


def test_disposition_all_failed_returns_failed() -> None:
    executions = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec("git_integration_worker", "failed", manage={"reason": "not running"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_empty_executions_returns_failed() -> None:
    assert _disposition_for([]) == "failed"


def test_disposition_all_executed_unchanged() -> None:
    assert _disposition_for([_exec("mcp", "executed")]) == "executed"


def test_disposition_all_queued_unchanged() -> None:
    assert _disposition_for([_exec("mcp", "queued", reason="draining")]) == "queued"


def test_disposition_all_submitted_returns_propagated() -> None:
    assert _disposition_for([_exec("mcp", "submitted")]) == "propagated"


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


def test_summary_propagated_without_failures_keeps_submitted_wording() -> None:
    executions = [_exec("mcp", "submitted")]
    summary = _summary_for("propagated", executions)
    assert "submitted or queued" in summary.lower()
