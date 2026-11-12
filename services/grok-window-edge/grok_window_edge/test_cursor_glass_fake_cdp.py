"""Fake in-process CDP for cursor_glass wake_tab / rename_tab handler paths."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from grok_window_edge.cursor_glass import cursor_glass

pytestmark = pytest.mark.offline

_AGENTS_PAGE = {
    "title": "Cursor Agents",
    "web_socket_debugger_url": "ws://127.0.0.1:9339/devtools/page/1",
}
_ORIGIN = "http://127.0.0.1:9339"
_TARGET = {"ok": True, "origin": _ORIGIN, "display": "8", "app": "cursor"}


def _enter_allowlisted_target_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch("grok_window_edge.cursor_glass.normalize_target", return_value=_TARGET)
    )
    stack.enter_context(
        patch(
            "grok_window_edge.cursor_glass.subscription_display_for_gui_host",
            return_value="8",
        )
    )
    stack.enter_context(
        patch("cursor_bridge.glass_cdp_core.normalize_target", return_value=_TARGET)
    )
    stack.enter_context(
        patch(
            "cursor_bridge.glass_cdp_core.subscription_display_for_gui_host",
            return_value="8",
        )
    )


@pytest.mark.asyncio
async def test_wake_tab_ok_pastes_when_ws_matches_allowlisted_origin() -> None:
    async def _paste(_ws: str, _body: str, *, agent_id: str = "") -> dict[str, Any]:
        return {"ok": True, "reason": "delivered"}

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "grok_window_edge.cursor_glass.unknown_gui_host_error",
                return_value={"ok": True, "gui_host": ":8"},
            )
        )
        _enter_allowlisted_target_patches(stack)
        stack.enter_context(
            patch("cursor_bridge.cdp_pages.list_pages", return_value=[_AGENTS_PAGE])
        )
        stack.enter_context(
            patch(
                "cursor_bridge.wake_delivery._pick_page",
                return_value=_AGENTS_PAGE,
            )
        )
        stack.enter_context(
            patch(
                "cursor_bridge.glass_cdp_core._paste_cursor_services",
                new=AsyncMock(side_effect=_paste),
            )
        )
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "", "body": "wake line"},
        )
    assert out["ok"] is True
    assert out.get("paste_attempted") is True


@pytest.mark.asyncio
async def test_wake_tab_no_page() -> None:
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "grok_window_edge.cursor_glass.unknown_gui_host_error",
                return_value={"ok": True, "gui_host": ":8"},
            )
        )
        _enter_allowlisted_target_patches(stack)
        stack.enter_context(
            patch("cursor_bridge.cdp_pages.list_pages", return_value=[])
        )
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "aid", "body": "wake"},
        )
    assert out["ok"] is False
    assert out["error"] == "no_page"


@pytest.mark.asyncio
async def test_wake_tab_paste_failed() -> None:
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "grok_window_edge.cursor_glass.unknown_gui_host_error",
                return_value={"ok": True, "gui_host": ":8"},
            )
        )
        _enter_allowlisted_target_patches(stack)
        stack.enter_context(
            patch("cursor_bridge.cdp_pages.list_pages", return_value=[_AGENTS_PAGE])
        )
        stack.enter_context(
            patch(
                "cursor_bridge.wake_delivery._pick_page",
                return_value=_AGENTS_PAGE,
            )
        )
        stack.enter_context(
            patch(
                "cursor_bridge.glass_cdp_core._paste_cursor_services",
                new=AsyncMock(side_effect=TimeoutError("paste")),
            )
        )
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "", "body": "wake"},
        )
    assert out["ok"] is False
    assert out["error"] == "paste_failed"
    assert out.get("paste_attempted") is True


@pytest.mark.asyncio
async def test_wake_tab_refused_http_port_before_dial() -> None:
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.normalize_target",
            return_value={
                "ok": True,
                "origin": "http://127.0.0.1:9338",
                "display": "8",
                "app": "cursor",
            },
        ),
        patch(
            "grok_window_edge.cursor_glass.subscription_display_for_gui_host",
            return_value="8",
        ),
        patch("cursor_bridge.cdp_pages.list_pages") as core_list,
    ):
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "a", "body": "line"},
        )
    core_list.assert_not_called()
    assert out["ok"] is False
    assert out["code"] == "cdp_port_refused"


@pytest.mark.asyncio
async def test_wake_tab_refuses_ws_on_wrong_port_before_connect() -> None:
    mismatch = {
        "title": "Cursor Agents",
        "web_socket_debugger_url": "ws://127.0.0.1:9340/devtools/page/1",
    }
    paste = AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "grok_window_edge.cursor_glass.unknown_gui_host_error",
                return_value={"ok": True, "gui_host": ":8"},
            )
        )
        _enter_allowlisted_target_patches(stack)
        stack.enter_context(
            patch("cursor_bridge.cdp_pages.list_pages", return_value=[mismatch])
        )
        stack.enter_context(
            patch("cursor_bridge.wake_delivery._pick_page", return_value=mismatch)
        )
        stack.enter_context(
            patch("cursor_bridge.glass_cdp_core._paste_cursor_services", new=paste)
        )
        out = await cursor_glass(
            "wake_tab",
            {"gui_host": ":8", "agent_id": "", "body": "wake"},
        )
    paste.assert_not_called()
    assert out["ok"] is False
    assert out["error"] == "cdp_port_refused"


@pytest.mark.asyncio
async def test_wake_tab_refuses_non_loopback_ws_before_connect() -> None:
    bad = {
        "title": "Cursor Agents",
        "web_socket_debugger_url": "ws://203.0.113.9:9339/devtools/page/1",
    }
    paste = AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "grok_window_edge.cursor_glass.unknown_gui_host_error",
                return_value={"ok": True, "gui_host": ":8"},
            )
        )
        _enter_allowlisted_target_patches(stack)
        stack.enter_context(
            patch("cursor_bridge.cdp_pages.list_pages", return_value=[bad])
        )
        stack.enter_context(
            patch("cursor_bridge.wake_delivery._pick_page", return_value=bad)
        )
        stack.enter_context(
            patch("cursor_bridge.glass_cdp_core._paste_cursor_services", new=paste)
        )
        out = await cursor_glass(
            "wake_tab", {"gui_host": ":8", "agent_id": "", "body": "x"}
        )
    paste.assert_not_called()
    assert out["error"] == "cdp_port_refused"


@pytest.mark.asyncio
async def test_rename_tab_ok_and_title_request_reaches_core() -> None:
    requested = "operator title"
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.cdp_origin_for_gui_host",
            return_value=_ORIGIN,
        ),
        patch(
            "cursor_bridge.glass_cdp_core.cursor_debug_url_for_gui_host",
            return_value=_ORIGIN,
        ),
        patch(
            "cursor_bridge.glass_cdp_core.rename_agent_row_in_sidebar",
            return_value={"ok": True, "new_title": "cdp echo", "reason": ""},
        ) as rename,
    ):
        out = await cursor_glass(
            "rename_tab",
            {"gui_host": ":8", "agent_id": "aid", "title": requested},
        )
    rename.assert_called_once()
    assert rename.call_args.args[1] == requested
    assert rename.call_args.args[0] == "aid"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_rename_tab_refuses_ws_port_mismatch_before_connect() -> None:
    with (
        patch(
            "grok_window_edge.cursor_glass.unknown_gui_host_error",
            return_value={"ok": True, "gui_host": ":8"},
        ),
        patch(
            "grok_window_edge.cursor_glass.cdp_origin_for_gui_host",
            return_value=_ORIGIN,
        ),
        patch(
            "cursor_bridge.glass_cdp_core.cursor_debug_url_for_gui_host",
            return_value=_ORIGIN,
        ),
        patch(
            "cursor_bridge.cursor_agents_hop.list_pages",
            return_value=[
                {
                    "title": "Cursor Agents",
                    "web_socket_debugger_url": "ws://127.0.0.1:9340/devtools/page/1",
                }
            ],
        ),
    ):
        out = await cursor_glass(
            "rename_tab",
            {"gui_host": ":8", "agent_id": "aid", "title": "t"},
        )
    assert out["ok"] is False
    assert out.get("reason") == "cdp_port_refused"
