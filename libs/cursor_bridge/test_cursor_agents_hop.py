"""Offline tests for Xvfb :8 CDP hop helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bus_watch import ide_hop

from cursor_bridge.cursor_agents_hop import (
    archive_cursor_agents_tab,
    is_cursor_xvfb_host,
    launch_cursor_agents_tab,
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
