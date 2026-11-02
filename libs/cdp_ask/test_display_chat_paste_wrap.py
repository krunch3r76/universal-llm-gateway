"""Regression: one provenance header/footer per display-chat paste path."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.display_chat_models import DisplayChatPasteRequest
from cdp_ask.display_chat_paste import execute_display_chat_paste
from cursor_bridge.operator_paste import (
    OPERATOR_HEADER,
    RELAY_NONCE_LABEL,
    WATERMARK,
    wrap_operator_message,
)

pytestmark = pytest.mark.offline


def _counts(text: str) -> tuple[int, int, int]:
    return (
        text.count(OPERATOR_HEADER),
        text.count(WATERMARK),
        text.count(RELAY_NONCE_LABEL),
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return TestClient(create_app())


@pytest.mark.asyncio
async def test_execute_display_chat_paste_single_wrap_on_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[str] = []
    pre_wrapped = wrap_operator_message(
        text="relay body",
        authorization_source="agent-bus:21614#2",
        scope="route",
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
    post = {
        **surface,
        "generating": True,
        "recent_messages": [pre_wrapped],
        "message_turns": [{"role": "user", "text": pre_wrapped}],
    }
    probe_n = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        probe_n["n"] += 1
        return surface if probe_n["n"] <= 6 else post

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

    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=pre_wrapped,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="route",
    )
    await execute_display_chat_paste(req)
    assert len(inserted) == 1
    assert _counts(inserted[0]) == (1, 1, 1)


def test_http_display_chat_paste_single_wrap_on_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[str] = []

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
    pre_wrapped = wrap_operator_message(
        text="http body",
        authorization_source="agent-bus:21614#2",
        scope="http",
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
    post = {
        **surface,
        "generating": True,
        "recent_messages": [pre_wrapped],
        "message_turns": [{"role": "user", "text": pre_wrapped}],
    }
    probe_n = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        probe_n["n"] += 1
        return surface if probe_n["n"] <= 6 else post

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

    resp = client.post(
        "/v1/display-chat/paste",
        json={
            "display": "10",
            "app": "grok",
            "agent_id": "agent-1",
            "text": pre_wrapped,
            "grant": "operator",
            "authorization_source": "agent-bus:21614#2",
            "scope": "http",
        },
    )
    assert resp.status_code == 200
    assert len(inserted) == 1
    assert _counts(inserted[0]) == (1, 1, 1)
