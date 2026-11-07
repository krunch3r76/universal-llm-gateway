"""Wake path: Grok CDP select ordering inside the display lock."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.wake_delivery import _deliver_locked

pytestmark = pytest.mark.offline


def _surface(**extra: Any) -> dict[str, Any]:
    base = {
        "state": "chat_list",
        "active_title": "Other",
        "active_agent_id": "other",
        "titles": ["Other", "Hidden"],
        "title_counts": {"Other": 1, "Hidden": 1},
        "agent_ids": {"other": "Other", "hidden-uuid": "Hidden"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["ready"],
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_cdp_select_before_sidebar() -> None:
    matched = _surface(active_title="Hidden", active_agent_id="hidden-uuid")
    surfaces = [_surface(), _surface(), matched]
    order: list[str] = []

    async def _probe(_ws: str) -> dict[str, Any]:
        if surfaces:
            return surfaces.pop(0)
        return matched

    async def _cdp(*_a: object, **_k: object) -> dict[str, Any]:
        order.append("cdp")
        return {"ok": True, "currentAgentId": "hidden-uuid"}

    async def _side(*_a: object, **_k: object) -> dict[str, Any]:
        order.append("sidebar")
        return {"clicked": True}

    with (
        patch("cursor_bridge.wake_delivery.list_pages", return_value=[{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}]),
        patch("cursor_bridge.wake_delivery.probe_surface", side_effect=_probe),
        patch("cursor_bridge.wake_delivery.select_grok_agent_async", side_effect=_cdp),
        patch("cursor_bridge.wake_delivery.select_sidebar_target", side_effect=_side),
        patch("cursor_bridge.wake_delivery.dom_insert_and_send", new_callable=AsyncMock, return_value={"ok": True, "delivered": True, "pasted": True}),
    ):
        out = await _deliver_locked(
            target={"ok": True, "display": "9", "app": "grok", "origin": "http://127.0.0.1:9339"},
            thread_s="1",
            slug_s="",
            title_s="Hidden",
            aid_s="hidden-uuid",
            select=True,
            line="hello",
        )
    assert order == ["cdp"]
    assert out.get("pasted") is not False or out.get("ok") is not False


@pytest.mark.asyncio
async def test_draft_inside_lock_selects_nothing() -> None:
    surfaces = [_surface(), _surface(composer_draft="human words")]
    cdp = AsyncMock()
    side = AsyncMock()
    with (
        patch("cursor_bridge.wake_delivery.list_pages", return_value=[{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}]),
        patch("cursor_bridge.wake_delivery.probe_surface", side_effect=surfaces.pop(0) if False else None),
    ):
        pass
    async def _probe(_ws: str) -> dict[str, Any]:
        return surfaces.pop(0)

    with (
        patch("cursor_bridge.wake_delivery.list_pages", return_value=[{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}]),
        patch("cursor_bridge.wake_delivery.probe_surface", side_effect=_probe),
        patch("cursor_bridge.wake_delivery.select_grok_agent_async", cdp),
        patch("cursor_bridge.wake_delivery.select_sidebar_target", side),
    ):
        out = await _deliver_locked(
            target={"ok": True, "display": "9", "app": "grok", "origin": "http://127.0.0.1:9339"},
            thread_s="1",
            slug_s="",
            title_s="Hidden",
            aid_s="hidden-uuid",
            select=True,
            line="hello",
        )
    assert out["reason"] == "composer_not_empty"
    assert out["pasted"] is False
    cdp.assert_not_awaited()
    side.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_cdp_falls_back_to_sidebar() -> None:
    surface = _surface()
    side = AsyncMock(return_value={"clicked": False})
    with (
        patch("cursor_bridge.wake_delivery.list_pages", return_value=[{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}]),
        patch("cursor_bridge.wake_delivery.probe_surface", new_callable=AsyncMock, return_value=surface),
        patch("cursor_bridge.wake_delivery.select_grok_agent_async", new_callable=AsyncMock, return_value={"ok": False}),
        patch("cursor_bridge.wake_delivery.select_sidebar_target", side),
    ):
        await _deliver_locked(
            target={"ok": True, "display": "9", "app": "grok", "origin": "http://127.0.0.1:9339"},
            thread_s="1",
            slug_s="",
            title_s="Hidden",
            aid_s="hidden-uuid",
            select=True,
            line="hello",
        )
    assert side.await_args.kwargs["expected_agent_id"] == "hidden-uuid"


@pytest.mark.asyncio
async def test_same_chat_false_does_not_select() -> None:
    surfaces = [
        _surface(),
        _surface(active_title="Switched", active_agent_id="someone-else"),
    ]

    async def _probe(_ws: str) -> dict[str, Any]:
        return surfaces.pop(0)

    cdp = AsyncMock()
    side = AsyncMock()
    with (
        patch("cursor_bridge.wake_delivery.list_pages", return_value=[{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}]),
        patch("cursor_bridge.wake_delivery.probe_surface", side_effect=_probe),
        patch("cursor_bridge.wake_delivery.select_grok_agent_async", cdp),
        patch("cursor_bridge.wake_delivery.select_sidebar_target", side),
    ):
        out = await _deliver_locked(
            target={"ok": True, "display": "9", "app": "grok", "origin": "http://127.0.0.1:9339"},
            thread_s="1",
            slug_s="",
            title_s="Hidden",
            aid_s="hidden-uuid",
            select=True,
            line="hello",
        )
    assert out["pasted"] is False
    cdp.assert_not_awaited()
    side.assert_not_awaited()
