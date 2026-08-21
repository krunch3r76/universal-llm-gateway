"""Manage JSON-RPC contract tests for recycle_giw."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.model_manager.ui import api_dispatch
from scripts.model_manager.ui.controller import giw_recycle


def test_recycle_giw_refuses_foreign_service() -> None:
    with pytest.raises(ValueError, match="hard-scoped"):
        giw_recycle.refuse_foreign_service("stargate", {})


def test_recycle_giw_refuses_extra_params() -> None:
    with pytest.raises(ValueError, match="accepts no parameters"):
        giw_recycle.refuse_foreign_service("", {"force": True})


def test_recycle_giw_allows_empty_or_giw_service() -> None:
    giw_recycle.refuse_foreign_service("", {})
    giw_recycle.refuse_foreign_service("git_integration_worker", {})
    giw_recycle.refuse_foreign_service("", {"service": "git_integration_worker"})


def test_occupant_progress_fresh_auto_heartbeat() -> None:
    fresh, token = giw_recycle.occupant_progress_fresh(
        {"active_ops": [{"op_id": "j1"}], "active_count": 1},
        {"queue_health": {"occupant_idle_s": 2.0}},
        idle_s=180.0,
        previous_token=None,
    )
    assert fresh is True
    stale, _ = giw_recycle.occupant_progress_fresh(
        {"active_ops": [{"op_id": "j1"}], "active_count": 1},
        {"queue_health": {"occupant_idle_s": 400.0}},
        idle_s=180.0,
        previous_token=token,
    )
    assert stale is False


def test_occupant_progress_fresh_heartbeat_fingerprint_change() -> None:
    snap1 = {
        "active_ops": [{"op_id": "d1", "last_heartbeat_at": "t1"}],
        "active_count": 1,
    }
    fresh1, token = giw_recycle.occupant_progress_fresh(
        snap1, None, idle_s=180.0, previous_token=None
    )
    assert fresh1 is False
    snap2 = {
        "active_ops": [{"op_id": "d1", "last_heartbeat_at": "t2"}],
        "active_count": 1,
    }
    fresh2, _ = giw_recycle.occupant_progress_fresh(
        snap2, None, idle_s=180.0, previous_token=token
    )
    assert fresh2 is True


def test_execute_recycle_giw_rejects_foreign_service() -> None:
    ctl = SimpleNamespace(root=Path("."), service_state=object())
    with pytest.raises(ValueError, match="hard-scoped"):
        asyncio.run(api_dispatch.execute(ctl, "recycle_giw", "stargate", {}))


_QUEUE_TOKENS = (
    "claim_next",
    "claim_next_concurrent",
    "claim_job",
    "get_queue()",
    "AutoJobQueue",
    "cursor_auto.queue",
    "agent_bus.request",
    "ledger.admit",
)


def test_manage_recycle_source_bypasses_auto_queue() -> None:
    """Wedged-queue case: manage-side recycle must not admit via AutoJobQueue."""
    source = Path(giw_recycle.__file__).read_text()
    for token in _QUEUE_TOKENS:
        assert token not in source, token
    assert "run_gated_drain_supervised" in source
    assert "idle_escalate_s" in source


def test_execute_recycle_giw_does_not_call_claim_next(monkeypatch) -> None:
    """api_dispatch.recycle_giw arms drain in the manage process, not claim_next."""
    calls: list[str] = []

    def _boom(*_args, **_kwargs):
        calls.append("claim_next")
        raise AssertionError("claim_next must not run on recycle_giw")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.queue.AutoJobQueue.claim_next",
        _boom,
        raising=False,
    )

    async def _drain(*_args, **_kwargs):
        return {"status": "deferred", "restart_intent_id": "intent-test"}

    monkeypatch.setattr(giw_recycle, "run_gated_drain_supervised", _drain)

    async def _emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(giw_recycle.events, "emit_manage_recycle_requested", _emit)
    monkeypatch.setattr(giw_recycle.events, "emit_manage_recycle_drain_attempted", _emit)

    ctl = SimpleNamespace(
        service_state=object(),
        build_git_worker_drain_supervisor=lambda **_k: object(),
        git_worker_kill_for=lambda _action: object(),
        restart_gate=object(),
        restart_intent_store=object(),
    )
    result = asyncio.run(api_dispatch.execute(ctl, "recycle_giw", "", {}))
    assert result["status"] == "deferred"
    assert result["recycle"] is True
    assert result["service"] == "git_integration_worker"
    assert calls == []
