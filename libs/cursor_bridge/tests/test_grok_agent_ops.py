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


@pytest.mark.asyncio
async def test_eval_display_uses_container_origin_and_reports_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host stays loopback; bridge client rewrites :9; list_pages failure is cdp_unreachable."""
    seen: dict[str, str] = {}

    def _pages(origin: str, timeout_s: float = 5.0) -> list[dict[str, str]]:
        del timeout_s
        seen["origin"] = origin
        return [
            {
                "title": "Grok Bot",
                "web_socket_debugger_url": "ws://example/devtools/page/1",
            }
        ]

    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"127.0.0.1"}),
    )
    with (
        patch("cursor_bridge.grok_agent_ops.list_pages", side_effect=_pages),
        patch(
            "cursor_bridge.grok_agent_ops._cdp_eval",
            new_callable=AsyncMock,
            return_value={"ok": True, "agents": [], "currentAgentId": None},
        ),
    ):
        host = await list_grok_agents_async("9")
    assert host["ok"] is True
    assert seen["origin"] == "http://127.0.0.1:9339"

    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"172.18.0.5"}),
    )
    with (
        patch("cursor_bridge.grok_agent_ops.list_pages", side_effect=_pages),
        patch(
            "cursor_bridge.grok_agent_ops._cdp_eval",
            new_callable=AsyncMock,
            return_value={"ok": True, "agents": [], "currentAgentId": None},
        ),
    ):
        container = await list_grok_agents_async("10")
    assert container["ok"] is True
    assert seen["origin"] == "http://172.18.0.1:9340"

    with patch(
        "cursor_bridge.grok_agent_ops.list_pages",
        side_effect=OSError("down"),
    ):
        down = await list_grok_agents_async("9")
    assert down["ok"] is False
    assert down["code"] == "cdp_unreachable"


@pytest.mark.asyncio
async def test_spawn_post_create_failure_resumes_same_agent() -> None:
    creates = {"n": 0}

    async def _eval(_display: object, expr: str, **_kw: Any) -> dict[str, Any]:
        if "createAgent" in expr:
            creates["n"] += 1
            return {"ok": True, "created": True, "agent_id": "kept-1"}
        return {"ok": True, "currentAgentId": "kept-1"}

    with (
        patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval),
        patch(
            "interlocutor.display_adapter.steer_display",
            new_callable=AsyncMock,
            side_effect=[
                {"ok": False, "code": "delivery_unconfirmed"},
                {"ok": True, "delivered": True},
            ],
        ),
    ):
        first = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="post-1"
        )
        second = await spawn_grok_agent_async(
            "9", "bot", text="hi", idempotency_key="post-1"
        )
    assert first["ok"] is False
    assert first["agent_id"] == "kept-1"
    assert second["ok"] is True
    assert second["agent_id"] == "kept-1"
    assert creates["n"] == 1


@pytest.mark.asyncio
async def test_spawn_key_conflict_and_operator_relay_before_create() -> None:
    with patch(
        "cursor_bridge.grok_agent_ops._eval_display", new_callable=AsyncMock
    ) as ev:
        ev.return_value = {"ok": True, "created": True, "agent_id": "a"}
        await spawn_grok_agent_async("9", "one", idempotency_key="same-key")
        clash = await spawn_grok_agent_async("9", "other", idempotency_key="same-key")
        refused = await spawn_grok_agent_async(
            "9", "relay", text="hi", voice="operator_relay"
        )
    assert clash["code"] == "idempotency_conflict"
    assert refused["code"] == "watermark_missing_authorization"
    assert ev.await_count == 2


def test_list_defaults_include_hidden_not_retired() -> None:
    js = __import__("cursor_bridge.grok_agent_ops", fromlist=["_js_list"])._js_list(
        include_hidden=True, include_retired=False
    )
    assert "includeHidden = true" in js
    assert "includeRetired = false" in js
