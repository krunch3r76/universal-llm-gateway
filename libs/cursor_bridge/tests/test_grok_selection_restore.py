"""Grok commission delivery restores prior selection after target switch."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.electron_chat_relay import (
    ElectronEndpoint,
    paste_endpoint_message,
    read_endpoint_turn,
)

pytestmark = pytest.mark.offline


def _noop_lock(*_a: Any, **_k: Any) -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _inner():
        yield

    return _inner()


@pytest.mark.asyncio
async def test_paste_commission_restores_prior_after_success() -> None:
    ep = ElectronEndpoint(
        label="gw3",
        app="grok",
        display="14",
        agent_id="hidden-target",
    )
    restored: list[str] = []
    pre_surface = {
        "composer_draft": "",
        "generating": False,
        "active_agent_id": "hidden-target",
        "message_turns": [],
    }
    post_surface = {
        **pre_surface,
        "message_turns": [{"role": "user", "text": "hi"}],
    }

    async def _resolve(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "ws_url": "ws://fake",
            "surface": pre_surface,
            "verdict": {"ok": True, "active_agent_id": "hidden-target"},
            "display": "14",
            "app": "grok",
            "selected": True,
            "restore_agent_id": "supervisor",
            "did_switch": True,
            "target_agent_id": "hidden-target",
        }

    async def _restore(display: str, prior: str, target: str, result: Any) -> None:
        del display, target, result
        restored.append(prior)

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            side_effect=_resolve,
        ),
        patch(
            "cursor_bridge.electron_chat_relay._restore_grok_prior_selection",
            side_effect=_restore,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            return_value=post_surface,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "hi", select=True)

    assert out["ok"] is True
    assert restored == ["supervisor"]


@pytest.mark.asyncio
async def test_paste_commission_restores_prior_after_failed_readback() -> None:
    ep = ElectronEndpoint(
        label="gw3",
        app="grok",
        display="14",
        agent_id="hidden-target",
    )
    restored: list[str] = []
    target_surface = {
        "composer_draft": "",
        "generating": False,
        "active_agent_id": "wrong-agent",
        "message_turns": [],
    }

    async def _resolve(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "ws_url": "ws://fake",
            "surface": target_surface,
            "verdict": {"ok": True, "active_agent_id": "hidden-target"},
            "display": "14",
            "app": "grok",
            "selected": True,
            "restore_agent_id": "supervisor",
            "did_switch": True,
        }

    async def _restore(_d: str, prior: str, _t: str, _r: Any) -> None:
        restored.append(prior)

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            side_effect=_resolve,
        ),
        patch(
            "cursor_bridge.electron_chat_relay._restore_grok_prior_selection",
            side_effect=_restore,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            return_value=target_surface,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "hi", select=True)

    assert out["ok"] is False
    assert out["code"] == "unknown"
    assert restored == ["supervisor"]


@pytest.mark.asyncio
async def test_grok_switch_bypasses_busy_prior_agent() -> None:
    from cursor_bridge.electron_chat_relay import _resolve_and_select_endpoint

    ep = ElectronEndpoint(
        label="gw3",
        app="grok",
        display="14",
        agent_id="hidden-target",
    )
    busy_supervisor = {
        "composer_draft": "",
        "generating": True,
        "active_agent_id": "supervisor",
        "state": "generating",
    }
    target_verdict = {"ok": True, "active_agent_id": "hidden-target"}

    with (
        patch(
            "cursor_bridge.electron_chat_relay.list_pages",
            return_value=[
                {
                    "title": "Grok Bot",
                    "web_socket_debugger_url": "ws://fake",
                }
            ],
        ),
        patch(
            "cursor_bridge.electron_chat_relay.normalize_target",
            return_value={
                "ok": True,
                "display": "14",
                "app": "grok",
                "origin": "http://127.0.0.1:9344",
            },
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            return_value=busy_supervisor,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.wake_decision",
            return_value={
                "ok": False,
                "state": "generating",
                "active_agent_id": "supervisor",
            },
        ),
        patch(
            "cursor_bridge.electron_chat_relay._grok_select_and_confirm",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "surface": {**busy_supervisor, "generating": False, "active_agent_id": "hidden-target"},
                "verdict": target_verdict,
                "selected": True,
                "did_switch": True,
            },
        ),
    ):
        res = await _resolve_and_select_endpoint(ep, select=True)

    assert res["ok"] is True
    assert res["did_switch"] is True


@pytest.mark.asyncio
async def test_read_restores_prior_selection() -> None:
    ep = ElectronEndpoint(
        label="gw3",
        app="grok",
        display="14",
        agent_id="hidden-target",
    )
    restored: list[str] = []

    async def _restore(_d: str, prior: str, _t: str, _r: Any) -> None:
        restored.append(prior)

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "surface": {
                    "recent_messages": ["hello"],
                    "generating": False,
                },
                "verdict": {"ok": True, "active_agent_id": "hidden-target"},
                "display": "14",
                "app": "grok",
                "selected": True,
                "restore_agent_id": "supervisor",
                "did_switch": True,
            },
        ),
        patch(
            "cursor_bridge.electron_chat_relay._restore_grok_prior_selection",
            side_effect=_restore,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await read_endpoint_turn(ep, select=True)

    assert out["ok"] is True
    assert restored == ["supervisor"]


@pytest.mark.asyncio
async def test_spawn_restores_prior_after_hidden_create_commission() -> None:
    from cursor_bridge.grok_agent_ops import spawn_grok_agent_async
    from cursor_bridge.grok_spawn_idempotency import clear_spawn_cache_for_tests

    clear_spawn_cache_for_tests()
    select_agents: list[str] = []
    steer = AsyncMock(return_value={"ok": True, "delivered": True, "pasted": True})

    async def _select(_display: object, agent_id: str, **_kw: Any) -> dict[str, Any]:
        select_agents.append(str(agent_id))
        return {"ok": True, "currentAgentId": agent_id}

    async def _list(_display: object, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "currentAgentId": "supervisor", "agents": []}

    async def _eval(_display: object, expr: str, **_kw: Any) -> Any:
        if "createAgent" in expr:
            return {"ok": True, "created": True, "agent_id": "hidden-new"}
        if "setAgentHiddenFromSidebar" in expr:
            return {"ok": True, "hidden": True, "code": "hidden"}
        return {"ok": True, "currentAgentId": "hidden-new"}

    with (
        patch(
            "cursor_bridge.grok_agent_ops.list_grok_agents_async",
            side_effect=_list,
        ),
        patch(
            "cursor_bridge.grok_agent_ops.select_grok_agent_async",
            side_effect=_select,
        ),
        patch("cursor_bridge.grok_agent_ops._eval_display", side_effect=_eval),
        patch(
            "cursor_bridge.grok_spawn_steps.preflight_subscriptions",
            return_value={"ok": True},
        ),
        patch(
            "cursor_bridge.grok_spawn_steps.subscribe_grok",
            return_value={"ok": True, "status_code": 200},
        ),
        patch("interlocutor.display_adapter.steer_display", steer),
    ):
        out = await spawn_grok_agent_async(
            "14",
            "Spawn Check 14",
            text="hello",
            mailbox="box",
            thread_scope="t1",
            idempotency_key="spawn-restore-1",
            hidden=True,
        )

    assert out["ok"] is True
    assert out.get("restore_ok") is True
    assert select_agents[0] == "hidden-new"
    assert select_agents[-1] == "supervisor"
    steer.assert_awaited_once()
