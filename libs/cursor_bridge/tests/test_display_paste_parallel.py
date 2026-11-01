"""Cross-process agent locks and operator-idle / chat-restore behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge.display_interaction_locks import agent_paste_lock
from cursor_bridge.operator_paste import WATERMARK, deliver_operator_paste

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_different_agent_ids_hold_locks_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_a() -> None:
        async with agent_paste_lock("10", "agent-a"):
            started.set()
            await release.wait()

    task = asyncio.create_task(hold_a())
    await asyncio.wait_for(started.wait(), 2.0)
    async with agent_paste_lock("10", "agent-b"):
        pass
    release.set()
    await asyncio.wait_for(task, 2.0)


@pytest.mark.asyncio
async def test_same_agent_id_serializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    order: list[str] = []
    gate = asyncio.Event()

    async def worker(tag: str) -> None:
        async with agent_paste_lock("10", "same-agent"):
            order.append(f"{tag}-in")
            if tag == "first":
                gate.set()
                await asyncio.sleep(0.05)
            order.append(f"{tag}-out")

    t1 = asyncio.create_task(worker("first"))
    await asyncio.wait_for(gate.wait(), 2.0)
    t2 = asyncio.create_task(worker("second"))
    await asyncio.gather(t1, t2)
    assert order.index("first-out") < order.index("second-in")


@pytest.mark.asyncio
async def test_deliver_operator_paste_operator_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": False, "reason": "operator_busy", "idle": False}),
    )
    insert = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    assert out["reason"] == "operator_busy"
    assert out["submitted"] is False
    assert out["pasted"] is False
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_operator_paste_restores_chat_after_hidden_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden_id = "hidden-agent-restore"
    operator_id = "operator-active"
    pre = {
        "state": "chat_list",
        "active_title": "Operator Chat",
        "active_agent_id": operator_id,
        "titles": ["Operator Chat"],
        "title_counts": {"Operator Chat": 1},
        "agent_ids": {operator_id: "Operator Chat"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    target = {
        **pre,
        "active_agent_id": hidden_id,
        "active_title": "Hidden Bot",
        "agent_ids": {hidden_id: "Hidden Bot", operator_id: "Operator Chat"},
    }
    post = {
        **target,
        "generating": True,
        "recent_messages": [WATERMARK],
    }
    probes = [pre, target, post]

    async def _probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else post

    select_calls: list[dict[str, str]] = []

    async def _select(_ws: str, **kwargs: Any) -> dict[str, Any]:
        select_calls.append(
            {
                "expected_agent_id": str(kwargs.get("expected_agent_id") or ""),
            }
        )
        return {"clicked": True, "matched_by": "agent_id"}

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.locate_off_sidebar",
        AsyncMock(return_value={"ok": True, "locate_method": "hidden_chats"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.select_sidebar_target", _select)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id=hidden_id,
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is True
    restore = [c for c in select_calls if c["expected_agent_id"] == operator_id]
    assert restore, select_calls
