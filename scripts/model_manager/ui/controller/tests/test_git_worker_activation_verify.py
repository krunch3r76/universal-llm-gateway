"""Tests for git_worker_activation_verify module."""

from __future__ import annotations

import asyncio

import pytest

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
    from charter_runner_store.propagation_validation import mint_pending_validation_for_intent

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
