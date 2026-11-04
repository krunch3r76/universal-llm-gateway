"""MCP paste_as_operator must not double-wrap provenance on the composer line."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from cursor_bridge.operator_paste import (
    OPERATOR_HEADER,
    RELAY_NONCE_LABEL,
    WATERMARK,
    wrap_operator_message,
)
from tools.cursor_bridge import _paste_as_operator

pytestmark = pytest.mark.offline


def _counts(text: str) -> tuple[int, int, int]:
    return (
        text.count(OPERATOR_HEADER),
        text.count(WATERMARK),
        text.count(RELAY_NONCE_LABEL),
    )


def test_mcp_paste_as_operator_single_wrap_on_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[str] = []
    pre_wrapped = wrap_operator_message(
        text="mcp body",
        authorization_source="agent-bus:21614#2",
        scope="mcp",
    )
    surface = {
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
    probe_n = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        probe_n["n"] += 1
        if probe_n["n"] <= 6 or not inserted:
            return surface
        sent = inserted[0]
        return {
            **surface,
            "generating": True,
            "recent_messages": [sent],
            "message_turns": [{"role": "user", "text": sent}],
        }

    async def _insert(_ws: str, *, selector: str, line: str) -> dict[str, Any]:
        inserted.append(line)
        return {"ok": True}

    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: True,
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(side_effect=_probe)
    )
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.install_operator_activity_probe",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.read_operator_busy",
        AsyncMock(return_value={"ok": True, "operator_active": False}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", _insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    monkeypatch.setattr("cdp_ask.display_chat_paste.emit", lambda _e: None)

    out = _paste_as_operator(
        display="10",
        app="grok",
        agent_id="agent-1",
        body=pre_wrapped,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="mcp",
    )
    assert out.get("ok") is True
    assert len(inserted) == 1
    assert _counts(inserted[0]) == (1, 1, 1)
