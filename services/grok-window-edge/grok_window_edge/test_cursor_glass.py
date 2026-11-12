"""Edge ``cursor_glass`` handlers with guarded CDP (fake pages)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from grok_window_edge.cdp import CdpPortRefusedError, assert_cdp_origin_allowed
from grok_window_edge.cursor_glass import cursor_glass

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_wake_tab_pastes_with_guarded_cdp() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}
    target = {
        "ok": True,
        "origin": "http://127.0.0.1:9338",
        "display": "8",
        "app": "cursor",
    }
    with (
        patch(
            "cursor_bridge.bridge_glass_relay.subscription_display_for_gui_host",
            return_value="8",
        ),
        patch("cursor_bridge.bridge_glass_relay.normalize_target", return_value=target),
        patch("cursor_bridge.cdp_pages.list_pages", return_value=[page]),
        patch("cursor_bridge.bridge_glass_relay.asyncio.run") as run,
    ):
        run.return_value = {"ok": True, "reason": "delivered"}
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "agent-1", "body": "wake line"},
        )
    assert out["ok"] is True
    assert out["op"] == "wake_tab"


@pytest.mark.asyncio
async def test_wake_tab_requires_body() -> None:
    out = await cursor_glass("wake_tab", {"gui_host": ":8", "agent_id": "a"})
    assert out["ok"] is False
    assert out["error"] == "body_required"


@pytest.mark.asyncio
async def test_rename_tab_calls_sidebar() -> None:
    with patch(
        "grok_window_edge.cursor_glass.relay_rename_tab",
        return_value={"ok": True, "new_title": "t", "op": "rename_tab"},
    ) as rename:
        out = await cursor_glass(
            "rename_tab",
            {"gui_host": ":8", "agent_id": "aid", "title": "new title"},
        )
    rename.assert_called_once()
    assert out["ok"] is True


def test_cdp_9341_still_refused_on_origin() -> None:
    with pytest.raises(CdpPortRefusedError):
        assert_cdp_origin_allowed("http://127.0.0.1:9341")


def test_cdp_9338_allowed_on_origin() -> None:
    assert_cdp_origin_allowed("http://127.0.0.1:9338")
