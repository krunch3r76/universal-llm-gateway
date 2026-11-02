"""Grok operator paste relay when local CDP is absent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from cdp_ask.display_chat_models import DisplayChatPasteRequest
from cdp_ask.grok_operator_actuation import relay_grok_operator_paste_if_needed

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_relay_skipped_when_local_grok_cdp_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: True,
    )
    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="a1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    assert await relay_grok_operator_paste_if_needed(req) is None


@pytest.mark.asyncio
async def test_relay_forwards_when_local_cdp_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.operator_actuation_paste_url",
        lambda: "http://10.0.0.67:8770/v1/display-chat/paste",
    )

    class _Resp:
        status_code = 200
        content = b'{"ok": true, "reason": "delivered", "delivered": true, "readback_ok": true}'

        def json(self) -> dict[str, object]:
            return {"ok": True, "reason": "delivered", "delivered": True, "readback_ok": True}

        def raise_for_status(self) -> None:
            return None

    mock_post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr("cdp_ask.grok_operator_actuation.httpx.AsyncClient", lambda **_: _FakeClient(mock_post))

    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="a1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    out = await relay_grok_operator_paste_if_needed(req)
    assert out is not None
    assert out.ok is True
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_hub_path_single_wrap_on_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: Jupiter relay must not re-wrap an already formatted body on hub deliver."""
    from cursor_bridge.operator_paste import (
        OPERATOR_HEADER,
        RELAY_NONCE_LABEL,
        WATERMARK,
        wrap_operator_message,
    )

    inserted: list[str] = []
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
    post = {**surface, "generating": True, "recent_messages": ["ok"]}
    probe_n = {"n": 0}

    async def _probe(_ws: str) -> dict:
        probe_n["n"] += 1
        return surface if probe_n["n"] <= 2 else post

    async def _insert(_ws: str, *, selector: str, line: str) -> dict:
        inserted.append(line)
        return {"ok": True}

    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.operator_actuation_paste_url",
        lambda: None,
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

    from cdp_ask.display_chat_paste import execute_display_chat_paste
    from cdp_ask.display_chat_models import DisplayChatPasteRequest

    pre_wrapped = wrap_operator_message(
        text="relay hop",
        authorization_source="agent-bus:21614#2",
        scope="relay",
    )
    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=pre_wrapped,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="relay",
    )
    await execute_display_chat_paste(req)
    assert len(inserted) == 1
    assert inserted[0].count(OPERATOR_HEADER) == 1
    assert inserted[0].count(WATERMARK) == 1
    assert inserted[0].count(RELAY_NONCE_LABEL) == 1


class _FakeClient:
    def __init__(self, post: AsyncMock) -> None:
        self._post = post

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> object:
        return await self._post(*args, **kwargs)
