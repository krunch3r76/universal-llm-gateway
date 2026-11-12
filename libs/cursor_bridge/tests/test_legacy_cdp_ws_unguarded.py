"""Legacy CDP dial paths stay unguarded (flag-off relay on 9338 / docker forwarder)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cursor_bridge.bridge_glass_relay import relay_rename_tab
from cursor_bridge.cursor_agents_archive import (
    archive_cursor_agents_tab,
    rename_agent_row_in_sidebar,
)

pytestmark = pytest.mark.offline

_AGENTS_PAGE_9338 = {
    "title": "Cursor Agents",
    "web_socket_debugger_url": "ws://127.0.0.1:9338/devtools/page/1",
}
_FORWARDER_PAGE = {
    "title": "Cursor Agents",
    "web_socket_debugger_url": "ws://172.18.0.1:9338/devtools/page/1",
}


def _sync_run_stub(
    coro: object, *_args: object, **_kwargs: object
) -> dict[str, object]:
    close = getattr(coro, "close", None)
    if callable(close):
        close()
    return {"ok": True, "reason": ""}


def test_rename_agent_row_in_sidebar_9338_not_refused() -> None:
    with (
        patch(
            "cursor_bridge.cursor_agents_hop.list_pages",
            return_value=[_AGENTS_PAGE_9338],
        ),
        patch(
            "cursor_bridge.cursor_agents_archive._hop.asyncio.run",
            side_effect=_sync_run_stub,
        ),
    ):
        out = rename_agent_row_in_sidebar(
            "aid",
            "title",
            debug_url="http://127.0.0.1:9338",
        )
    assert out.get("reason") != "cdp_port_refused"
    assert out["ok"] is True


def test_relay_rename_tab_9338_through_real_ws_url_resolve() -> None:
    with (
        patch(
            "cursor_bridge.cursor_agents_hop.list_pages",
            return_value=[_AGENTS_PAGE_9338],
        ),
        patch(
            "cursor_bridge.cursor_agents_archive._hop.asyncio.run",
            side_effect=_sync_run_stub,
        ),
    ):
        out = relay_rename_tab(gui_host=":8", agent_id="aid", title="new title")
    assert out.get("reason") != "cdp_port_refused"
    assert out["ok"] is True


def test_archive_tab_forwarder_origin_172_18_not_refused() -> None:
    with (
        patch(
            "cursor_bridge.cursor_agents_hop.list_pages",
            return_value=[_FORWARDER_PAGE],
        ),
        patch(
            "cursor_bridge.cursor_agents_archive._hop.asyncio.run",
            side_effect=lambda coro, *_a, **_k: _sync_run_stub(coro) | {"archived": []},
        ),
    ):
        out = archive_cursor_agents_tab("aid", debug_url="http://172.18.0.1:9338")
    assert out.get("reason") != "cdp_port_refused"
    assert out["ok"] is True
