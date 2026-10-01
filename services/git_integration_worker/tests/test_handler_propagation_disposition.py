"""Unit tests for propagate envelope disposition and summary derived from executions[]."""

from __future__ import annotations

from unittest.mock import patch

from services.git_integration_worker.relay.manage_sock import sync_restart_service


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






# --- AC2: weakest row floors mixed sets ---










# --- uniform sets ---


































# --- AC3: D7/turn-27 payload replay ---




# --- summary honesty ---










def test_sync_restart_service_forwards_propagate_row_identity() -> None:
    """Manage mint receives the ledger row SHA and row_id, not a HEAD-only payload."""
    captured: dict = {}

    def _call(method: str, params: dict | None = None, *, timeout: float = 0.0) -> dict:
        captured["method"] = method
        captured["params"] = params or {}
        return {"status": "ok"}

    with patch(
        "services.git_integration_worker.relay.manage_sock.call_manage",
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
    assert "caller_job_id" not in captured["params"]


def test_sync_restart_service_forwards_caller_job_id() -> None:
    """agent_bus propagate identifies the executing cursor-auto job to manage."""
    captured: dict = {}

    def _call(method: str, params: dict | None = None, *, timeout: float = 0.0) -> dict:
        captured["method"] = method
        captured["params"] = params or {}
        return {"status": "ok"}

    with patch(
        "services.git_integration_worker.relay.manage_sock.call_manage",
        _call,
    ):
        sync_restart_service(
            "agent_bus",
            caller_job_id="job-propagate-12286",
        )
    assert captured["params"]["caller_job_id"] == "job-propagate-12286"
    assert captured["params"]["service"] == "agent_bus"
