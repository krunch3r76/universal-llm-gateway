"""Cancel resolution, recovery, and list_restart_intents projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from scripts.model_manager.ui.controller.restart_intent_cancel_support import (
    orchestrate_cancel_restart_intent,
    resolve_cancel_target,
)
from scripts.model_manager.ui.controller.restart_intent_expiry import (
    BOUNDARY_INTENT_TTL_S,
)
from scripts.model_manager.ui.controller.restart_intent_list import list_restart_intents
from scripts.model_manager.ui.controller.restart_intent_states import STATUS_EXPIRED
from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore


def _store(path: Any) -> RestartIntentStore:
    return RestartIntentStore(path / "restart-intents.db")


@pytest.mark.asyncio
async def test_prefix_cancel_unique(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="mcp",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    prefix = intent.intent_id[:8]
    result = await orchestrate_cancel_restart_intent(store, intent_id=prefix)
    assert result["status"] == "cancelled"
    assert store.get(intent.intent_id).status == "cancelled"  # type: ignore[union-attr]


def test_prefix_cancel_ambiguous(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)

    class _Row:
        def __init__(self, intent_id: str) -> None:
            self.intent_id = intent_id

    monkeypatch.setattr(
        store,
        "pending_intents",
        lambda: [
            _Row("abcdef12-1111-1111-1111-111111111111"),
            _Row("abcdef12-2222-2222-2222-222222222222"),
        ],
    )
    _intent, err = resolve_cancel_target(store, intent_id="abcdef12", service=None)
    assert _intent is None
    assert err is not None
    assert err["reason"] == "intent_id_ambiguous"
    assert len(err["candidates"]) == 2


@pytest.mark.asyncio
async def test_service_only_cancel(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="live",
    )
    result = await orchestrate_cancel_restart_intent(
        store, intent_id="", service="git_integration_worker"
    )
    assert result["status"] == "cancelled"
    assert result["intent_id"] == intent.intent_id


@pytest.mark.asyncio
async def test_cancel_expired_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="mcp", action="restart", deadline_at="d", reason="x"
    )
    store.advance_if_status(
        intent.intent_id,
        from_status="pending_drain",
        to_status=STATUS_EXPIRED,
        reason="ttl_expired",
    )
    result = await orchestrate_cancel_restart_intent(store, intent_id=intent.intent_id)
    assert result["status"] == "expired"
    assert result["already_terminal"] is True


@pytest.mark.asyncio
async def test_release_failure_recovers_when_worker_already_released(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=2, worker_id="w", worker_started_at="t"
    )

    async def _boom(_iid: str, _epoch: int) -> dict[str, Any]:
        raise RuntimeError("cancel-drain down")

    async def _probe() -> dict[str, Any]:
        return {"draining": False, "armed": False, "intent_id": None}

    result = await orchestrate_cancel_restart_intent(
        store,
        intent_id=intent.intent_id,
        release_drain=_boom,
        probe_drain_state=_probe,
    )
    assert result["status"] == "cancelled"
    assert result["drain_release"]["recovered_after_error"]
    assert store.get(intent.intent_id).status == "cancelled"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_release_failure_still_armed_is_retryable(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="git_integration_worker",
        action="restart",
        deadline_at="d",
        reason="r",
    )
    store.set_drain_epoch(
        intent.intent_id, drain_epoch=4, worker_id="w", worker_started_at="t"
    )

    async def _boom(_iid: str, _epoch: int) -> dict[str, Any]:
        raise RuntimeError("timeout")

    async def _probe() -> dict[str, Any]:
        return {
            "draining": True,
            "armed": True,
            "intent_id": intent.intent_id,
            "drain_epoch": 4,
        }

    result = await orchestrate_cancel_restart_intent(
        store,
        intent_id=intent.intent_id,
        release_drain=_boom,
        probe_drain_state=_probe,
    )
    assert result["status"] == "error"
    assert result["reason"] == "drain_release_failed"
    assert result["error"]
    assert result["retryable"] is True
    assert store.get(intent.intent_id).status == "pending_drain"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_repeat_cancel_already_cancelled(tmp_path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="mcp", action="restart", deadline_at="d", reason="x"
    )
    first = await orchestrate_cancel_restart_intent(store, intent_id=intent.intent_id)
    assert first["status"] == "cancelled"
    again = await orchestrate_cancel_restart_intent(store, intent_id=intent.intent_id)
    assert again["status"] == "cancelled"
    assert again["already_cancelled"] is True


def test_list_restart_intents_projection(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    before = datetime.now(UTC)
    live = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at="ceiling",
        reason="list",
        wait_for_boundary=True,
        caller_agent="cursor",
    )
    past = (before - timedelta(seconds=BOUNDARY_INTENT_TTL_S + 10)).isoformat()
    store._update(live.intent_id, expires_at=None, armed_at=past)
    expired_row = store.get(live.intent_id)
    assert expired_row is not None

    payload = list_restart_intents(store, service="git_integration_worker", now=before)
    assert payload["intents"]
    row = payload["intents"][0]
    assert row["intent_id"] == live.intent_id
    assert row["expired"] is True
    assert row["ttl_remaining_s"] is None
    assert row["caller_agent"] == "cursor"
    assert "git_integration_worker" in payload["recent_terminal"]


def test_resolve_cancel_target_service_none(tmp_path) -> None:
    store = _store(tmp_path)
    _intent, err = resolve_cancel_target(
        store, intent_id="", service="git_integration_worker"
    )
    assert _intent is None
    assert err is not None
    assert err["reason"] == "no_live_intent"
