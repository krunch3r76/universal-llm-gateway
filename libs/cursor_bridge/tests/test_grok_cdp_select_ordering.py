"""Grok agent_id must use roster CDP selection before DOM sidebar fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.electron_chat_relay import ElectronEndpoint, read_endpoint_turn

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_hidden_agent_cdp_select_before_sidebar() -> None:
    endpoint = ElectronEndpoint(
        label="hidden-bot",
        app="grok",
        display="9",
        agent_id="hidden-uuid",
        thread="hidden-uuid",
    )
    pages = [
        {
            "title": "Grok Bot",
            "web_socket_debugger_url": "ws://127.0.0.1:9339/devtools/page/1",
        }
    ]
    surfaces = [
        {
            "state": "chat_list",
            "active_title": "Other",
            "active_agent_id": "other",
            "titles": ["Other"],
            "agent_ids": {"other": "Other"},
            "composer_draft": "",
            "generating": False,
            "recent_messages": ["ok"],
        },
        {
            "state": "chat_list",
            "active_title": "Hidden",
            "active_agent_id": "hidden-uuid",
            "titles": ["Hidden"],
            "agent_ids": {"hidden-uuid": "Hidden"},
            "composer_draft": "",
            "generating": False,
            "recent_messages": ["latest"],
        },
    ]

    async def _probe(_ws: str) -> dict[str, Any]:
        return surfaces.pop(0)

    sidebar = AsyncMock(return_value={"clicked": True})

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.electron_chat_relay.probe_surface", side_effect=_probe),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "hidden-uuid"},
        ) as cdp_select,
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            side_effect=sidebar,
        ),
    ):
        snap = await read_endpoint_turn(endpoint, select=True)

    assert snap["ok"] is True
    cdp_select.assert_awaited_once()
    sidebar.assert_not_awaited()
    assert snap["active_agent_id"] == "hidden-uuid"
