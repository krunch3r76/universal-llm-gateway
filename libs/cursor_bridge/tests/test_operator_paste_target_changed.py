"""Regression: wrong-agent composer writes must return target_changed, not submit."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge.operator_paste import WATERMARK, deliver_operator_paste

pytestmark = pytest.mark.offline


def _surface(*, agent_id: str) -> dict[str, Any]:
    return {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": agent_id,
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {agent_id: "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }


@pytest.fixture(autouse=True)
def _operator_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.read_operator_busy",
        AsyncMock(return_value={"ok": True, "operator_active": False}),
    )


@pytest.mark.asyncio
async def test_target_changed_before_insert_clears_ours_and_never_inserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _surface(agent_id="agent-target")
    wrong = _surface(agent_id="agent-wrong")
    insert = AsyncMock(return_value={"ok": True})
    clear = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-target"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._probe_agent_match",
        AsyncMock(return_value=(False, wrong)),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", clear
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-target",
        text="must not land on wrong chat",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "target_changed"
    assert out["pasted"] is False
    assert out["submitted"] is False
    insert.assert_not_awaited()
    clear.assert_awaited()


@pytest.mark.asyncio
async def test_target_changed_after_insert_clears_ours_no_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _surface(agent_id="agent-target")
    wrong = _surface(agent_id="agent-wrong")
    calls = {"n": 0}

    async def _probe_match(_ws: str, *, expected_agent_id: str) -> tuple[bool, dict]:
        calls["n"] += 1
        if calls["n"] == 1:
            return True, target
        return False, wrong

    insert = AsyncMock(return_value={"ok": True})
    send = AsyncMock(return_value={"ok": True, "send_fallback_clicked": True})
    clear = AsyncMock(return_value={"ok": True, "cleared": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-target"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=target),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._probe_agent_match",
        AsyncMock(side_effect=_probe_match),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", send
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", clear
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-target",
        text=f"draft with {WATERMARK} must not submit elsewhere",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "target_changed"
    assert out["pasted"] is False
    assert out["submitted"] is False
    send.assert_not_awaited()
    clear.assert_awaited()


@pytest.mark.asyncio
async def test_operator_busy_at_guard_insert_skips_without_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _surface(agent_id="agent-1")
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.read_operator_busy",
        AsyncMock(return_value={"ok": True, "operator_active": True}),
    )
    insert = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "operator_busy"
    assert out["pasted"] is False
    assert out["submitted"] is False
    insert.assert_not_awaited()
