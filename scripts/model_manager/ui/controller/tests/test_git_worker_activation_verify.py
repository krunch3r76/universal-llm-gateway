"""Tests for git_worker_activation_verify module."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from charter_runner_store.propagation_validation import (
    mint_pending_validation_for_intent,
)

from scripts.model_manager.ui.controller.git_worker_activation_verify import (
    ACTIVATION_IDLE_TIMEOUT_S,
    arms_activation_verify,
    run_activation_verify,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_VERIFYING_ACTIVATION,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore


def _run(coro):
    return asyncio.run(coro)


def test_arms_activation_verify_restart_only() -> None:
    assert arms_activation_verify("restart")
    assert arms_activation_verify("sync_restart")
    assert not arms_activation_verify("stop")


def test_missing_kill_boundary_times_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )

    validation_id = mint_pending_validation_for_intent(intent)
    _run(
        run_activation_verify(
            store,
            intent.intent_id,
            validation_id,
            idle_timeout_s=0.01,
        )
    )
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == "activation_unverified"


def test_expired_kill_boundary_budget_terminalizes_without_reset(tmp_path, monkeypatch) -> None:
    """An already-expired settle budget must fail closed on entry, not extend the clock."""
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_VERIFYING_ACTIVATION,
    )
    expired_boundary = (datetime.now(UTC) - timedelta(seconds=ACTIVATION_IDLE_TIMEOUT_S + 30)).isoformat()
    store.set_kill_boundary(intent.intent_id, kill_boundary_at=expired_boundary)

    unreachable_probe = {"probe_reachable": False}
    with patch(
        "services.git_integration_worker.cursor_auto.propagation_probe.probe_process_live",
        return_value=unreachable_probe,
    ):
        validation_id = mint_pending_validation_for_intent(intent)
        _run(
            run_activation_verify(
                store,
                intent.intent_id,
                validation_id,
                idle_timeout_s=ACTIVATION_IDLE_TIMEOUT_S,
            )
        )
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == "activation_unverified"
    assert got.reason == "idle_timeout"
