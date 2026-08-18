"""Unit tests for propagate envelope disposition and summary derived from executions[]."""

from __future__ import annotations

from unittest.mock import patch

from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.handler_propagation import (
    _disposition_for,
    _summary_for,
    execution_for_manage_deferred,
    restart_intent_persisted,
)
from services.git_integration_worker.cursor_auto.manage_sock import sync_restart_service


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
    """Manage RPC errors must floor disposition to failed, never propagated."""
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
    """Manage RPC error floors disposition even when the row status looks optimistic."""
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
    """Mixed executed, submitted, and queued rows must floor disposition to queued."""
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
        _exec("stargate", "queued", reason="draining"),
    ]
    assert _disposition_for(executions) == "queued"


def test_disposition_mixed_executed_and_submitted_floors_to_submitted() -> None:
    """Mixed executed and submitted rows must floor disposition to submitted."""
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "submitted"),
    ]
    assert _disposition_for(executions) == "submitted"


def test_disposition_mixed_executed_and_failed_floors_to_failed() -> None:
    """Any failed row mixed with executed rows must floor disposition to failed."""
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_any_failed_row_never_propagated() -> None:
    """Failed-axis guard: no execution set containing failed may yield propagated."""
    failed_variants = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec(
            "cortex-api",
            "failed",
            manage={"status": "error", "reason": "manage_rpc_error"},
        ),
        _exec("gateway", "failed", reason="proof_class_unsupported"),
        _exec(
            "stargate",
            "submitted",
            manage={"status": "error", "reason": "manage_rpc_error"},
        ),
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
    """Uniform failed executions must map disposition to failed."""
    executions = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec("git_integration_worker", "failed", manage={"reason": "not running"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_empty_executions_returns_failed() -> None:
    """Empty execution lists must fail closed with disposition failed."""
    assert _disposition_for([]) == "failed"


def test_disposition_all_executed_maps_to_propagated() -> None:
    """All executed rows with proof observed must map disposition to propagated."""
    assert _disposition_for([_exec("mcp", "executed")]) == "propagated"


def test_disposition_all_submitted_returns_submitted() -> None:
    """Uniform submitted rows must keep disposition submitted while proof is pending."""
    assert _disposition_for([_exec("mcp", "submitted")]) == "submitted"


def test_disposition_submitted_never_propagated_while_proof_pending() -> None:
    """Open-row submitted executions must not yield propagated while proof is pending."""
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
    """Uniform queued rows must keep disposition queued without upgrade."""
    assert _disposition_for([_exec("mcp", "queued", reason="draining")]) == "queued"


def test_disposition_all_blocked_returns_blocked() -> None:
    """Uniform blocked rows must map disposition to blocked."""
    assert _disposition_for([_exec("mcp", "blocked", reason="busy")]) == "blocked"


def test_disposition_mixed_executed_and_blocked_floors_to_blocked() -> None:
    """Mixed executed and blocked rows must floor disposition to blocked."""
    executions = [
        _exec("mcp", "executed"),
        _exec("gateway", "blocked", reason="busy"),
    ]
    assert _disposition_for(executions) == "blocked"


def test_restart_intent_persisted_requires_intent_id() -> None:
    """Deferred manage results require restart_intent_id to count as persisted."""
    assert (
        restart_intent_persisted({"status": "deferred", "state": "draining"}) is False
    )
    assert (
        restart_intent_persisted({"status": "deferred", "restart_intent_id": "x"})
        is True
    )


def test_execution_for_manage_deferred_without_intent_is_harvest_wanted() -> None:
    """Deferred manage without restart intent must mark harvest_wanted for charter tick."""
    row = PropagationRow(
        service="mcp",
        code_ref="deadbeef",
        action="sync_restart",
        proof_class="client_visible",
    )
    with patch(
        "services.git_integration_worker.cursor_auto.handler_propagation.mark_harvest_wanted",
        return_value=True,
    ) as mock_mark:
        result = execution_for_manage_deferred(
            row,
            row_id="mcp:deadbeef:sync_restart",
            manage_result={
                "status": "deferred",
                "state": "busy",
                "reason": "cdp_ask_live",
            },
        )
    assert result["status"] == "harvest_wanted"
    assert "charter tick will consume" in result["next"].lower()
    mock_mark.assert_called_once_with("mcp:deadbeef:sync_restart")


def test_deferred_is_self_preemptable_for_mcp_cdp_ask_busy() -> None:
    """MCP and cdp_ask busy deferrals may self-preempt; other services may not."""
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        deferred_is_self_preemptable,
    )

    assert deferred_is_self_preemptable(
        "mcp",
        {"status": "deferred", "reason": "service has in-flight work; pass force=true"},
    )
    assert deferred_is_self_preemptable(
        "cdp_ask",
        {"status": "deferred", "active_work": {"busy_reasons": ["cdp_ask_live"]}},
    )
    assert not deferred_is_self_preemptable(
        "gateway",
        {"status": "deferred", "reason": "service has in-flight work; pass force=true"},
    )
    assert not deferred_is_self_preemptable(
        "mcp",
        {"status": "deferred", "restart_intent_id": "intent-1", "reason": "draining"},
    )


def test_summary_propagated_self_preempt_includes_mcp_disconnect_advisory() -> None:
    """Propagated self-preempt summaries must include MCP disconnect advisory text."""
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        MCP_DISCONNECT_ADVISORY,
        _self_preempt_escalations_for,
        _summary_for,
    )

    executions = [
        {
            "service": "mcp",
            "status": "executed",
            "self_preempt_applied": True,
            "preempted": "cdp_ask_live",
            "advisory": MCP_DISCONNECT_ADVISORY,
        }
    ]
    summary = _summary_for("propagated", executions)
    assert "self-preempt" in summary.lower()
    assert "cdp_ask_live" in summary
    assert "disconnect momentarily" in summary.lower()
    assert "MCP will disconnect momentarily" in summary
    escalations = _self_preempt_escalations_for(executions)
    assert escalations == [
        {"service": "mcp", "preempted": "cdp_ask_live", "force": "true"}
    ]


def test_summary_harvest_wanted_self_preempt_vetoed() -> None:
    """Harvest-wanted summaries must report when self-preempt was vetoed."""
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        _summary_for,
    )

    executions = [
        {
            "service": "mcp",
            "status": "harvest_wanted",
            "reason": "cdp_ask_live",
            "self_preempt_suppressed": True,
            "would_preempt": "cdp_ask_live",
        }
    ]
    summary = _summary_for("harvest_wanted", executions)
    assert "self-preempt vetoed" in summary.lower()
    assert "cdp_ask_live" in summary


def test_execution_for_manage_deferred_with_intent_is_queued() -> None:
    """Deferred manage with restart intent must queue supervisor-owned drain completion."""
    row = PropagationRow(
        service="git_integration_worker",
        code_ref="deadbeef",
        action="sync_restart",
        proof_class="process_live",
    )
    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        ) as mock_set,
        patch(
            "charter_runner_store.propagation_validation.queries.bind_validation_to_row",
            return_value=1,
        ),
    ):
        result = execution_for_manage_deferred(
            row,
            row_id="git_integration_worker:deadbeef:sync_restart",
            manage_result={
                "status": "deferred",
                "state": "draining",
                "restart_intent_id": "intent-abc",
                "activation_validation_id": "val-abc",
                "reason": "draining; completion delivered via git_worker.drain events",
            },
        )
    assert result["status"] == "queued"
    assert "supervisor-owned" in result["next"].lower()
    mock_set.assert_called_once_with(
        "git_integration_worker:deadbeef:sync_restart", "manage_queued_drain"
    )


def test_disposition_all_harvest_wanted_returns_harvest_wanted() -> None:
    """Uniform harvest_wanted rows must map disposition to harvest_wanted."""
    assert (
        _disposition_for([_exec("mcp", "harvest_wanted", reason="cdp_ask_live")])
        == "harvest_wanted"
    )


def test_summary_harvest_wanted_claims_charter_tick() -> None:
    """Harvest-wanted summaries must claim charter tick will consume the deferral."""
    executions = [_exec("mcp", "harvest_wanted", reason="cdp_ask_live")]
    summary = _summary_for("harvest_wanted", executions)
    assert "charter tick will consume" in summary.lower()
    assert "nothing will fire" not in summary.lower()


# --- AC3: D7/turn-27 payload replay ---


def test_disposition_d7_turn27_payload_replay() -> None:
    """Replay D7 turn-27 manage error payload must floor disposition to failed."""
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
    """Failed summaries must name failing services and surface their manage reasons."""
    executions = [_exec("mcp", "failed", manage={"reason": "socket refused"})]
    summary = _summary_for("failed", executions)
    assert "mcp" in summary
    assert "socket refused" in summary
    assert "submitted or queued" not in summary.lower()


def test_summary_mixed_executed_and_failed_surfaces_partial_and_failure() -> None:
    """Mixed executed and failed summaries must report partial progress and failure."""
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
    """Submitted summaries must state the ledger row remains open pending proof."""
    executions = [_exec("mcp", "submitted")]
    summary = _summary_for("submitted", executions)
    assert "submitted" in summary.lower()
    assert "ledger row open" in summary.lower()


def test_summary_propagated_claims_proof_observed() -> None:
    """Propagated summaries must claim proof-of-live was observed for executed rows."""
    executions = [_exec("mcp", "executed")]
    summary = _summary_for("propagated", executions)
    assert "proof-of-live observed" in summary.lower()


def test_sync_restart_service_forwards_propagate_row_identity() -> None:
    """Manage mint receives the ledger row SHA and row_id, not a HEAD-only payload."""
    captured: dict = {}

    def _call(method: str, params: dict | None = None, *, timeout: float = 0.0) -> dict:
        captured["method"] = method
        captured["params"] = params or {}
        return {"status": "ok"}

    with patch(
        "services.git_integration_worker.cursor_auto.manage_sock.call_manage",
        _call,
    ):
        sync_restart_service(
            "git_integration_worker",
            code_ref="8fc646c7aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            row_id="git_integration_worker:8fc646c7:sync_restart",
        )
    assert captured["method"] == "sync_restart"
    assert captured["params"]["code_ref"] == "8fc646c7aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert (
        captured["params"]["row_id"] == "git_integration_worker:8fc646c7:sync_restart"
    )
