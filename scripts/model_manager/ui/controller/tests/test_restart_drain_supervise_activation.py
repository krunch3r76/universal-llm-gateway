"""Activation validation resolve scheduling on generic drain supervisors."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.model_manager.ui.controller.restart_drain_supervise import (
    supervise_until_lifecycle,
)
from scripts.model_manager.ui.controller.restart_intent_states import STATUS_COMPLETED
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore

_RESOLVABLE_CODE_REF = "ab53680e92543c316b16aef9a1412cd652c2a56b"


async def _run_supervise(store, intent_id: str) -> None:
    probe = AsyncMock(return_value=MagicMock())
    probe.return_value.detail = None

    def is_idle(_work: object) -> bool:
        return True

    lifecycle = AsyncMock(return_value="ok")
    intent = store.get(intent_id)
    assert intent is not None
    await supervise_until_lifecycle(
        store=store,
        intent=intent,
        probe=probe,
        is_idle=is_idle,
        lifecycle=lifecycle,
        deadline_s=1.0,
        poll_interval_s=0.01,
        ceiling="extend",
    )


@pytest.mark.asyncio
async def test_supervise_schedules_resolve_for_non_giw(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "services.git_integration_worker.relay.propagation_probe.probe_process_live",
        lambda _service: {"probe_reachable": False},
    )
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="mcp",
        action="sync_restart",
        deadline_at="d",
        reason="r",
    )
    from charter_runner_store.propagation_validation import (
        mint_pending_validation_for_intent,
    )

    mint_pending_validation_for_intent(intent, code_ref=_RESOLVABLE_CODE_REF)
    with patch(
        "scripts.model_manager.ui.controller.git_worker_activation_verify.schedule_resolve_validation_after_lifecycle"
    ) as schedule:
        await _run_supervise(store, intent.intent_id)
        schedule.assert_called_once()
        args = schedule.call_args[0]
        assert args[1] == intent.intent_id
        assert args[2] == "mcp"
    got = store.get(intent.intent_id)
    assert got is not None
    assert got.status == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_supervise_does_not_schedule_resolve_for_giw(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    store = RestartIntentStore(db_path=tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    with patch(
        "scripts.model_manager.ui.controller.git_worker_activation_verify.schedule_resolve_validation_after_lifecycle"
    ) as schedule:
        await _run_supervise(store, intent.intent_id)
        schedule.assert_not_called()
