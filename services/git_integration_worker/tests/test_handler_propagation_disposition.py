"""Unit tests for propagate envelope disposition and summary honesty."""

from __future__ import annotations

import pytest

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


def test_disposition_all_failed_returns_failed() -> None:
    executions = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
        _exec("git_integration_worker", "failed", manage={"reason": "not running"}),
    ]
    assert _disposition_for(executions) == "failed"


def test_disposition_empty_executions_returns_failed() -> None:
    assert _disposition_for([]) == "failed"


def test_disposition_all_executed_unchanged() -> None:
    executions = [_exec("mcp", "executed")]
    assert _disposition_for(executions) == "executed"


def test_disposition_all_queued_unchanged() -> None:
    executions = [_exec("mcp", "queued", reason="draining")]
    assert _disposition_for(executions) == "queued"


def test_disposition_mixed_executed_and_failed_returns_propagated() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    assert _disposition_for(executions) == "propagated"


def test_disposition_all_submitted_returns_propagated() -> None:
    executions = [_exec("mcp", "submitted")]
    assert _disposition_for(executions) == "propagated"


def test_summary_all_failed_names_services_and_reasons() -> None:
    executions = [
        _exec("mcp", "failed", manage={"reason": "socket refused"}),
    ]
    summary = _summary_for("failed", executions)
    assert "mcp" in summary
    assert "socket refused" in summary
    assert "submitted or queued" not in summary.lower()


def test_summary_empty_executions_no_submitted_claim() -> None:
    summary = _summary_for("failed", [])
    assert "submitted or queued" not in summary.lower()
    assert "failed" in summary.lower()


def test_summary_mixed_executed_and_failed_surfaces_failure() -> None:
    executions = [
        _exec("mcp", "executed"),
        _exec("git_integration_worker", "failed", manage={"reason": "manage_error"}),
    ]
    summary = _summary_for("propagated", executions)
    assert "git_integration_worker" in summary
    assert "manage_error" in summary
    assert "failed" in summary.lower()


def test_summary_propagated_without_failures_keeps_submitted_wording() -> None:
    executions = [_exec("mcp", "submitted")]
    summary = _summary_for("propagated", executions)
    assert "submitted or queued" in summary.lower()
