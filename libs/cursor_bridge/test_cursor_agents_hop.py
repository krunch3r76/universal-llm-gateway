"""Offline tests for Xvfb :8 CDP hop helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bus_watch import ide_hop

from cursor_bridge.cursor_agents_hop import (
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
