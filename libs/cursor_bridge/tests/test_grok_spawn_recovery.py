"""Spawn recovery: concurrency, cancel, hide readback, unknown steer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.grok_agent_ops import spawn_grok_agent_async
from cursor_bridge.grok_spawn_idempotency import clear_spawn_cache_for_tests

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_spawn_cache_for_tests()


def _eval_factory(store: dict[str, Any]) -> Any:
    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            store["creates"] = int(store.get("creates") or 0) + 1
            if store.get("hold_create") is not None:
                await store["hold_create"].wait()
            return {"ok": True, "created": True, "agent_id": "agent-1"}
        if "setAgentHiddenFromSidebar" in expr:
            store["hides"] = int(store.get("hides") or 0) + 1
            hidden = bool(store.get("hidden", True))
            return {"ok": hidden, "hidden": hidden, "code": "hidden" if hidden else "hide_unverified"}
        return {"ok": True, "currentAgentId": "agent-1"}

    return _eval


@pytest.mark.asyncio
async def test_concurrent_fresh_calls_one_create() -> None:
    store: dict[str, Any] = {"hold_create": asyncio.Event()}

    async def _release() -> None:
        await asyncio.sleep(0.05)
        store["hold_create"].set()

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval_factory(store)):
        asyncio.get_running_loop().create_task(_release())
        first, second = await asyncio.gather(
            spawn_grok_agent_async("9", "bot", idempotency_key="race", hidden=True),
            spawn_grok_agent_async("9", "bot", idempotency_key="race", hidden=True),
        )
    codes = {first.get("code"), second.get("code")}
    assert store["creates"] == 1
    assert "idempotency_in_flight" in codes or first.get("ok") or second.get("ok")


@pytest.mark.asyncio
async def test_hide_failure_retries_same_agent_without_create() -> None:
    store: dict[str, Any] = {"hidden": False}

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval_factory(store)):
        first = await spawn_grok_agent_async("9", "bot", idempotency_key="hide-1", hidden=True)
        store["hidden"] = True
        second = await spawn_grok_agent_async("9", "bot", idempotency_key="hide-1", hidden=True)
    assert first["ok"] is False
    assert first["code"] == "hide_unverified"
    assert second["ok"] is True
    assert second["hidden"] is True
    assert store["creates"] == 1


@pytest.mark.asyncio
async def test_cancel_during_create_blocks_retry() -> None:
    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            raise asyncio.CancelledError()
        return {"ok": True}

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval):
        with pytest.raises(asyncio.CancelledError):
            await spawn_grok_agent_async("9", "bot", idempotency_key="cancel-create")
    with patch("cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock) as ev:
        retry = await spawn_grok_agent_async("9", "bot", idempotency_key="cancel-create")
    assert retry["code"] == "create_unknown"
    ev.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_not_sent_retries_steer_once() -> None:
    store: dict[str, Any] = {}
    steer = AsyncMock(
        side_effect=[
            {"ok": False, "code": "composer_not_empty", "delivered": False, "pasted": False},
            {"ok": True, "delivered": True, "pasted": True},
        ]
    )
    with (
        patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval_factory(store)),
        patch("interlocutor.display_adapter.steer_display", steer),
    ):
        first = await spawn_grok_agent_async("9", "bot", text="hi", idempotency_key="steer-ok")
        second = await spawn_grok_agent_async("9", "bot", text="hi", idempotency_key="steer-ok")
    assert first["ok"] is False
    assert second["ok"] is True
    assert store["creates"] == 1
    assert steer.await_count == 2


@pytest.mark.asyncio
async def test_subscription_unavailable_before_create() -> None:
    with (
        patch(
            "cursor_bridge.grok_spawn_steps.preflight_subscriptions",
            return_value={"ok": False, "code": "subscription_unavailable"},
        ),
        patch("cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock) as ev,
    ):
        out = await spawn_grok_agent_async(
            "9", "bot", mailbox="box", thread_scope="t1", idempotency_key="sub-pre"
        )
    assert out["code"] == "subscription_unavailable"
    ev.assert_not_awaited()
