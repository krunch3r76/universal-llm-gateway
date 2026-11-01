"""Friction 41247: restart intents honor expires_at (sweep + lazy re-arm)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.model_manager.ui.controller.restart_drain import (
    LocalServiceDrainSupervisor,
    RestartDrainGate,
    run_gated_drain_supervised,
)
from scripts.model_manager.ui.controller.restart_intent_expiry import (
    expire_pending_intent,
    intent_expired,
    sweep_expired_pending_intents,
)
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore


@pytest.mark.asyncio
async def test_sweep_expiry_emits_event(tmp_path, monkeypatch) -> None:
    store = RestartIntentStore(tmp_path / "intents.db")
    intent = store.create_intent(
        service="agent_bus",
        action="sync_restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="cursor-sdk",
    )
    past = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    emitted: list[dict[str, object]] = []

    async def _capture(**kwargs: object) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(
        "scripts.model_manager.observation_event.emit_manage_restart_expired",
        _capture,
    )
    ids = await sweep_expired_pending_intents(store)
    assert ids == [intent.intent_id]
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == "expired"
    assert emitted and emitted[0]["intent_id"] == intent.intent_id


@pytest.mark.asyncio
async def test_lazy_rearm_mints_fresh_intent_after_expiry(tmp_path, monkeypatch) -> None:
    store = RestartIntentStore(tmp_path / "intents.db")
    stale = store.create_intent(
        service="agent_bus",
        action="sync_restart",
        deadline_at="ceiling",
        reason="armed",
        caller_agent="manage",
    )
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._update(stale.intent_id, expires_at=past)

    gate = RestartDrainGate(probes={})
    gate.evaluate = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gate.release = AsyncMock()  # type: ignore[method-assign]

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service="agent_bus",
        store=store,
        lifecycle=AsyncMock(return_value="ok"),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain._spawn_supervised",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.open_service_window",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.restart_drain.mint_activation_validation",
        lambda *_a, **_k: "val-1",
    )

    result = await run_gated_drain_supervised(
        gate,
        "sync_restart",
        "agent_bus",
        store=store,
        supervisor=supervisor,
        reason="re-arm",
        caller_agent="cursor-sdk",
    )
    assert result["status"] == "deferred"
    new_id = result["restart_intent_id"]
    assert new_id != stale.intent_id
    assert store.get(stale.intent_id).status == "expired"  # type: ignore[union-attr]
    assert store.active_for_service("agent_bus").intent_id == new_id  # type: ignore[union-attr]


def test_wait_for_boundary_null_expires_not_due(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "intents.db")
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="ceiling",
        reason="armed",
        wait_for_boundary=True,
    )
    assert intent.expires_at is None
    assert not intent_expired(intent, now=datetime.now(UTC) + timedelta(days=1))


@pytest.mark.asyncio
async def test_unexpired_intent_still_dedupes(tmp_path, monkeypatch) -> None:
    store = RestartIntentStore(tmp_path / "intents.db")
    live = store.create_intent(
        service="agent_bus",
        action="sync_restart",
        deadline_at="ceiling",
        reason="armed",
    )
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store._update(live.intent_id, expires_at=future)

    gate = RestartDrainGate(probes={})
    in_progress = MagicMock()
    in_progress.to_result.return_value = {"state": "in_progress"}
    gate.evaluate = AsyncMock(return_value=in_progress)  # type: ignore[method-assign]

    supervisor = LocalServiceDrainSupervisor(
        gate=gate,
        service="agent_bus",
        store=store,
        lifecycle=AsyncMock(return_value="ok"),
    )
    result = await run_gated_drain_supervised(
        gate,
        "sync_restart",
        "agent_bus",
        store=store,
        supervisor=supervisor,
        reason="join",
    )
    assert result["restart_intent_id"] == live.intent_id
    assert result["reason"] == "drain already in progress for this service"
    assert store.active_for_service("agent_bus") is not None


@pytest.mark.asyncio
async def test_expire_pending_intent_records_transition(tmp_path) -> None:
    store = RestartIntentStore(tmp_path / "intents.db")
    intent = store.create_intent(
        service="stargate",
        action="sync_restart",
        deadline_at="ceiling",
        reason="armed",
    )
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    store._update(intent.intent_id, expires_at=past)
    assert await expire_pending_intent(store, intent.intent_id, emit_event=False)
    row = store.get(intent.intent_id)
    assert row is not None
    assert row.status == "expired"
    assert any(t.get("status") == "expired" for t in (row.transitions or []))
