"""Restore/select for hidden grok work; composer agent-id is the pane signal."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.chat_surface import classify_surface, wake_decision
from cursor_bridge.electron_chat_relay import ElectronEndpoint, read_endpoint_turn

pytestmark = pytest.mark.offline


def _noop_lock(*_a: Any, **_k: Any) -> Any:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _inner():
        yield

    return _inner()


def test_wake_decision_matches_when_composer_agent_id_attribute_present() -> None:
    """Positive signal: prompt ``data-sidebar-file-drop-editor-agent-id`` → active_agent_id."""
    surface = {
        "state": "chat_list",
        "active_agent_id": "hidden-target",
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
    )
    assert verdict["ok"] is True
    assert verdict["reason"] == "match"


def test_classify_surface_sand_agent_row_sets_active_id() -> None:
    aid = "35d84d28-d1e7-42a0-9794-156d8c70ff87"
    surface = classify_surface(
        [],
        [
            {
                "text": "Spawn Check 14b (retired)",
                "selected": True,
                "agent_id": aid,
            }
        ],
        has_active_chat=True,
    )
    assert surface["state"] == "chat_list"
    assert surface["active_agent_id"] == aid


def test_wake_decision_matches_selection_store_when_title_empty() -> None:
    aid = "35d84d28-d1e7-42a0-9794-156d8c70ff87"
    surface = {
        "state": "chat_list",
        "active_agent_id": aid,
        "active_title": "",
        "selection_agent_id": aid,
        "titles": [],
        "agent_ids": {aid: "Spawn Check 14b (retired)"},
    }
    verdict = wake_decision(surface, thread="23653", slug="x", agent_id=aid)
    assert verdict["ok"] is True
    assert verdict["reason"] == "match"


def test_wake_decision_refuses_empty_composer_and_title_for_hidden_target() -> None:
    surface = {
        "state": "chat_list",
        "active_agent_id": "",
        "active_title": "",
        "titles": [],
        "agent_ids": {},
    }
    verdict = wake_decision(
        surface,
        thread="23658",
        slug="spawn-check-14",
        agent_id="hidden-target",
    )
    assert verdict["ok"] is False
    assert verdict["reason"] == "no_active_chat"


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
