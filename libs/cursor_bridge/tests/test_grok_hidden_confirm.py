"""Hidden grok seats: selection-state confirm, wake gate, select op."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.chat_surface import wake_decision
from cursor_bridge.electron_chat_grok_wait import (
    grok_conversation_attributed,
    probe_grok_after_select,
)
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


def test_wake_decision_hidden_when_selection_matches_empty_title() -> None:
    surface = {
        "state": "chat_list",
        "active_agent_id": "",
        "active_title": "",
        "titles": [],
        "agent_ids": {},
        "recent_messages": [],
    }
    verdict = wake_decision(
        surface,
        thread="23658",
        slug="spawn-check-14",
        agent_id="hidden-target",
        selection_agent_id="hidden-target",
    )
    assert verdict["ok"] is True
    assert verdict["reason"] == "match"


def test_hidden_empty_pane_attributed_when_selection_on_target() -> None:
    surface = {
        "active_agent_id": "",
        "recent_messages": [],
        "generating": False,
    }
    assert (
        grok_conversation_attributed(
            surface,
            expected_agent_id="hidden-target",
            prior_message_fp="supervisor-fp",
            prior_generating=True,
            selection_agent_id="hidden-target",
        )
        is True
    )


@pytest.mark.asyncio
async def test_probe_hidden_empty_pane_not_stale_dom_when_selection_matches() -> None:
    lag = {
        "state": "chat_list",
        "active_agent_id": "",
        "active_title": "",
        "recent_messages": ["supervisor line"],
        "generating": True,
        "has_active_chat": True,
    }
    bound = {
        **lag,
        "active_agent_id": "hidden-target",
        "recent_messages": [],
        "generating": False,
    }
    probes = [dict(lag), dict(bound)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(bound)

    async def _sel(_d: object) -> str:
        return "hidden-target"

    with (
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait._selection_agent_id",
            side_effect=_sel,
        ),
        patch("cursor_bridge.electron_chat_grok_wait.asyncio.sleep", new_callable=AsyncMock),
    ):
        out = await probe_grok_after_select(
            "ws://fake",
            endpoint=ElectronEndpoint(
                label="t",
                app="grok",
                display="14",
                agent_id="hidden-target",
            ),
            expected_agent_id="hidden-target",
            baseline_agent_id="supervisor",
            prior_message_fp="supervisor-fp",
            prior_generating=True,
            display="14",
        )
    assert out.get("stale_dom") is not True
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_wake_delivery_hidden_subscription_not_no_active_chat() -> None:
    from cursor_bridge.wake_delivery import _deliver_locked

    empty_surface = {
        "state": "chat_list",
        "active_agent_id": "",
        "active_title": "",
        "titles": [],
        "agent_ids": {},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    with (
        patch(
            "cursor_bridge.wake_delivery.normalize_target",
            return_value={
                "ok": True,
                "display": "14",
                "app": "grok",
                "origin": "http://127.0.0.1:9344",
            },
        ),
        patch(
            "cursor_bridge.wake_delivery.list_pages",
            return_value=[
                {
                    "title": "Grok Bot",
                    "web_socket_debugger_url": "ws://fake",
                }
            ],
        ),
        patch(
            "cursor_bridge.wake_delivery.probe_surface",
            new_callable=AsyncMock,
            return_value=empty_surface,
        ),
        patch(
            "cursor_bridge.grok_agent_ops.grok_selection_current_agent_id_async",
            new_callable=AsyncMock,
            return_value="hidden-target",
        ),
        patch(
            "cursor_bridge.wake_delivery.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "hidden-target"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.probe_grok_after_select",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "stale_dom": False,
                "surface": empty_surface,
                "verdict": {
                    "ok": True,
                    "reason": "match",
                    "active_agent_id": "",
                },
                "attributed": True,
            },
        ),
        patch(
            "cursor_bridge.wake_delivery.dom_insert_and_send",
            new_callable=AsyncMock,
            return_value={"ok": True, "pasted": True},
        ),
        patch("cursor_bridge.wake_delivery.agent_paste_lock", _noop_lock),
        patch("cursor_bridge.wake_delivery.display_switch_lock", _noop_lock),
    ):
        out = await _deliver_locked(
            target={
                "ok": True,
                "display": "14",
                "app": "grok",
                "origin": "http://127.0.0.1:9344",
            },
            thread_s="23658",
            slug_s="spawn-check-14",
            title_s="",
            aid_s="hidden-target",
            select=True,
            line="BRIDGE_WAKE thread=23658 turn=1",
        )
    assert out.get("reason") != "no_active_chat"
    assert out.get("delivered") is True or out.get("pasted") is True


@pytest.mark.asyncio
async def test_read_restores_on_no_active_chat_resolve_failure() -> None:
    restored: list[str] = []

    async def _restore(_d: str, prior: str, _t: str, _r: Any) -> None:
        restored.append(prior)

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "reason": "no_active_chat",
                "restore_agent_id": "supervisor",
                "did_switch": True,
                "display": "14",
                "app": "grok",
            },
        ),
        patch(
            "cursor_bridge.electron_chat_relay._restore_grok_prior_selection",
            side_effect=_restore,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await read_endpoint_turn(
            ElectronEndpoint(
                label="gw3",
                app="grok",
                display="14",
                agent_id="hidden-target",
            ),
            select=True,
        )
    assert out.get("reason") == "no_active_chat"
    assert restored == ["supervisor"]


def test_interlocutor_select_sets_current_agent_id_only() -> None:
    from interlocutor.facade import execute_select

    with (
        patch(
            "cursor_bridge.grok_agent_ops.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "hidden-target"},
        ),
        patch(
            "cursor_bridge.grok_agent_ops.grok_selection_current_agent_id_async",
            new_callable=AsyncMock,
            return_value="hidden-target",
        ),
    ):
        out = execute_select(target="display:14/hidden-target/grok")
    assert out["ok"] is True
    assert out["currentAgentId"] == "hidden-target"
    assert "restore" not in out
