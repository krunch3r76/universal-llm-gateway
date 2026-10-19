"""Offline tests for Xvfb :8 CDP hop helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bus_watch import ide_hop

from cursor_bridge.cursor_agents_hop import (
    _archive_agent_js,
    _archive_async,
    _prune_predecessors_async,
    archive_cursor_agents_tab,
    is_cursor_xvfb_host,
    launch_cursor_agents_tab,
    prune_cursor_agents_predecessors,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "host",
    [":8", "8", "io:8", "127.0.0.1:9338", "http://127.0.0.1:9338"],
)
def test_is_cursor_xvfb_host(host: str) -> None:
    assert is_cursor_xvfb_host(host) is True
    assert is_cursor_xvfb_host("jupiter") is False


def test_session_unreachable_xvfb_returns_none() -> None:
    assert ide_hop.session_unreachable(":8") is None


def test_launch_cursor_agents_tab_ok() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {
            "ok": True,
            "transport": "cdp_cursor_agents",
            "page_title": "Cursor Agents",
            "editor_agent_id": "ed-1",
            "send_fallback_clicked": False,
        }

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = launch_cursor_agents_tab("resume 10479")
    assert out["ok"] is True
    assert out["transport"] == "cdp_cursor_agents"


def test_archive_cursor_agents_tab_empty_id() -> None:
    assert archive_cursor_agents_tab("") == {"ok": False, "reason": "empty_agent_id"}
    assert archive_cursor_agents_tab("   ") == {"ok": False, "reason": "empty_agent_id"}


def test_archive_cursor_agents_tab_ok() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {"ok": True, "archived": "agent-abc"}

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = archive_cursor_agents_tab("agent-abc")
    assert out == {"ok": True, "archived": "agent-abc"}


def test_archive_agent_js_streaming_guard() -> None:
    js = _archive_agent_js("agent-x")
    assert '[aria-label="In progress"], .ui-dot-grid-loader' in js
    assert "agent_streaming" in js
    assert "btn.click()" in js
    # streaming check precedes archive click
    assert js.index("agent_streaming") < js.index("btn.click()")


@pytest.mark.asyncio
async def test_archive_async_polls_until_idle() -> None:
    calls: list[int] = []

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        calls.append(mid)
        if len(calls) == 1:
            return {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}
        return {"ok": True, "archived": "agent-abc"}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop.asyncio.sleep", new_callable=AsyncMock),
    ):
        out = await _archive_async(
            "ws://fake",
            "agent-abc",
            timeout_s=5.0,
            wait_until_idle_s=10.0,
            poll_interval_s=0.01,
        )
    assert out == {"ok": True, "archived": "agent-abc"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_archive_async_streaming_timeout() -> None:
    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    with patch("websockets.connect", return_value=_WsCtx()), patch(
        "cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval
    ):
        out = await _archive_async(
            "ws://fake",
            "agent-abc",
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.01,
        )
    assert out == {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}


@pytest.mark.asyncio
async def test_prune_keeps_departing_archives_older() -> None:
    list_result = [
        {"agent_id": "new-active", "title": "10479 slug", "active": True, "streaming": False},
        {"agent_id": "n-1", "title": ". 10479 foo", "active": False, "streaming": False},
        {"agent_id": "n-2", "title": ". 10479 old", "active": False, "streaming": False},
        {"agent_id": "n-3", "title": "resume 10479", "active": False, "streaming": False},
    ]
    archived: list[str] = []

    async def fake_archive(ws_url, aid, **kwargs):  # type: ignore[no-untyped-def]
        archived.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="new-active",
            departing_agent_id="n-1",
            keep_recent_predecessors=1,
            timeout_s=5.0,
            wait_until_idle_s=60.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert out["kept_predecessors"] == ["n-1"]
    assert set(out["archived"]) == {"n-2", "n-3"}
    assert set(archived) == {"n-2", "n-3"}


def test_prune_cursor_agents_predecessors_empty_root() -> None:
    assert prune_cursor_agents_predecessors("") == {"ok": False, "reason": "empty_root_id"}


def test_archive_cursor_agents_tab_row_not_found() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {"ok": False, "reason": "row_not_found"}

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = archive_cursor_agents_tab("missing-id")
    assert out["reason"] == "row_not_found"
