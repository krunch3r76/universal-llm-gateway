"""Offline tests for operator paste wrap and grant gates."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge.operator_paste import (
    WATERMARK,
    _assistant_began,
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


def test_assistant_began_false_when_only_user_watermark_lands() -> None:
    pre = ["prior turn prose"]
    post = {
        "generating": False,
        "recent_messages": ["prior turn prose", f"relay {WATERMARK}"],
        "composer_draft": "",
    }
    assert _assistant_began(post, watermark=WATERMARK, pre_messages=pre) is False


def test_assistant_began_after_watermark_in_rolling_six_message_window() -> None:
    """Fails len-only check: tail stays length 6 while assistant prose appears after wm."""
    pre = [f"older-{i}" for i in range(6)]
    post = pre[:4] + [f"user {WATERMARK}", "assistant reply body"]
    surface = {
        "generating": False,
        "recent_messages": post,
        "composer_draft": "",
    }
    assert _assistant_began(surface, watermark=WATERMARK, pre_messages=pre) is True
    assert len(post) == len(pre)


@pytest.mark.asyncio
async def test_deliver_operator_paste_polls_until_assistant_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single early probe would miss streaming; poll until generating flips true."""
    surface_pre = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["prior turn prose"],
    }
    wm_only = {
        **surface_pre,
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["prior turn prose", f"relay {WATERMARK}"],
    }
    streaming = {**wm_only, "generating": True}

    seq = [surface_pre, wm_only, wm_only, streaming]
    idx = {"n": 0}

    async def _probe_counted(_ws: str) -> dict[str, Any]:
        i = min(idx["n"], len(seq) - 1)
        idx["n"] += 1
        return seq[i]

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
    sleeps: list[float] = []

    async def _fast_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("cursor_bridge.operator_paste.asyncio.sleep", _fast_sleep)

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert idx["n"] >= 3
    assert sleeps
    assert out["watermark_visible"] is True
    assert out["composer_empty_after"] is True
    assert out["assistant_began"] is True
    assert out["ok"] is True
