"""Offline tests for operator paste wrap and grant gates."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge.operator_paste import (
    WATERMARK,
    deliver_operator_paste,
    wrap_operator_message,
)

pytestmark = pytest.mark.offline


def test_wrap_operator_message_includes_header_source_watermark() -> None:
    msg = wrap_operator_message(
        text="hello",
        authorization_source="agent-bus:21614#2",
        scope="falsifier",
    )
    assert "Operator request — Kaywan" in msg
    assert "Source: agent-bus:21614#2" in msg
    assert "Scope: falsifier" in msg
    assert WATERMARK in msg
    assert "hello" in msg


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_without_grant() -> None:
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="abc",
        text="hi",
        grant="",
        authorization_source="agent-bus:1#1",
    )
    assert out["reason"] == "grant_or_source_required"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_cursor_ide() -> None:
    out = await deliver_operator_paste(
        display="8",
        app="cursor",
        agent_id="abc",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    assert out["reason"] == "cursor_ide_seat_forbidden"


@pytest.mark.asyncio
async def test_deliver_operator_paste_grok_ok_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface_pre = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    surface_post = {
        **surface_pre,
        "generating": True,
        "recent_messages": [f"line with {WATERMARK}"],
    }

    async def _probe(_ws: str) -> dict[str, Any]:
        return surface_post if _probe.calls else surface_pre  # type: ignore[attr-defined]

    _probe.calls = 0  # type: ignore[attr-defined]

    async def _probe_counted(ws: str) -> dict[str, Any]:
        _probe.calls += 1  # type: ignore[attr-defined]
        if _probe.calls <= 1:  # type: ignore[attr-defined]
            return surface_pre
        return surface_post

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe_counted),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._paste",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_artifacts",
        AsyncMock(return_value={"sha256_dom": "d" * 64, "sha256_png": "p" * 64}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is True
    assert out["readback_ok"] is True
    assert out["sha256_dom"] == "d" * 64
