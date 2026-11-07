"""Offline tests for Grok roster CDP ops."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.grok_agent_ops import (
    clear_spawn_cache_for_tests,
    list_grok_agents_async,
    select_grok_agent_async,
    spawn_grok_agent_async,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_spawn_cache() -> None:
    clear_spawn_cache_for_tests()


def test_list_unknown_display() -> None:
    out = asyncio.run(list_grok_agents_async("7"))
    assert out["ok"] is False
    assert out["code"] == "unknown_display"


@pytest.mark.asyncio
async def test_list_filters_hidden_and_retired() -> None:
    payload = {
        "ok": True,
        "agents": [{"id": "a1", "name": "visible"}],
        "currentAgentId": "a1",
    }

    async def _eval(_display: object, _expr: str, **_kw: Any) -> dict[str, Any]:
        return payload

    with patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval):
        out = await list_grok_agents_async(
            "9", include_hidden=False, include_retired=False
        )
    assert out["ok"] is True
    assert out["display"] == "9"
    assert out["currentAgentId"] == "a1"


@pytest.mark.asyncio
async def test_select_verifies_current_agent() -> None:
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display",
        new_callable=AsyncMock,
        return_value={
            "ok": True,
            "currentAgentId": "aid-1",
            "expectedAgentId": "aid-1",
        },
    ):
        out = await select_grok_agent_async("10", "aid-1")
    assert out["ok"] is True
    assert out["agent_id"] == "aid-1"


@pytest.mark.asyncio
async def test_spawn_idempotency_duplicate() -> None:
    calls = {"n": 0}

    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        calls["n"] += 1
        if "createAgent" in expr or "clientNonce" in expr:
            return {"ok": True, "agent_id": "spawned-1"}
        return {"ok": True, "currentAgentId": "spawned-1"}

    with (
        patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval),
        patch(
            "interlocutor.display_adapter.steer_display",
            new_callable=AsyncMock,
        ) as steer,
    ):
        steer.return_value = {"ok": True, "delivered": True}
        first = await spawn_grok_agent_async(
            "9", "bot-a", idempotency_key="idem-1", text="hello"
        )
        second = await spawn_grok_agent_async(
            "9", "bot-a", idempotency_key="idem-1", text="hello"
        )
    assert first["ok"] is True
    assert second.get("duplicate") is True
    assert calls["n"] == 2  # create + verify select on first call only paths


@pytest.mark.asyncio
async def test_spawn_partial_failure_clears_idempotency() -> None:
    with (
        patch(
            "cursor_bridge.grok_agent_ops._eval_display",
            new_callable=AsyncMock,
            return_value={"ok": False, "code": "roster_api_missing"},
        ),
    ):
        out = await spawn_grok_agent_async("9", "x", idempotency_key="idem-2")
    assert out["ok"] is False
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display",
        new_callable=AsyncMock,
        return_value={"ok": True, "agent_id": "r1"},
    ) as ev:
        retry = await spawn_grok_agent_async("9", "x", idempotency_key="idem-2")
    assert retry["ok"] is True
    assert ev.await_count >= 1


def test_list_defaults_include_hidden_not_retired() -> None:
    js = __import__("cursor_bridge.grok_agent_ops", fromlist=["_js_list"])._js_list(
        include_hidden=True, include_retired=False
    )
    assert "includeHidden = true" in js
    assert "includeRetired = false" in js
