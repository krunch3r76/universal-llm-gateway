"""wake_tab binding resolve and no focused-tab fallthrough."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.db import init_db
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def test_relay_wake_tab_unbound_thread_refuses(bus_db: None) -> None:
    from cursor_bridge.bridge_glass_relay import relay_wake_tab

    out = relay_wake_tab(
        gui_host=":8",
        agent_id="723224a2-ea2a-5b32-a19c-70e2ae09430e",
        body="wake line",
        thread="99999",
    )
    assert out["ok"] is False
    assert out["error"] == "unbound"


def test_relay_wake_tab_resolves_bound_thread(bus_db: None) -> None:
    from agent_bus_store.db import create_thread
    from cursor_bridge.bridge_glass_relay import relay_wake_tab

    create_thread(thread_id="21220", slug="wake-tab")
    subscribe_endpoint(
        agent="cursor",
        thread_scope="21220",
        surface="cursor",
        display=":8",
        title="house",
        agent_id="723224a2-ea2a-5b32-a19c-70e2ae09430e",
    )
    pages = [
        {"title": "Other App", "web_socket_debugger_url": "ws://focused/1"},
        {"title": "Cursor Agents", "web_socket_debugger_url": "ws://agents/1"},
    ]
    target = {"ok": True, "origin": "http://127.0.0.1:9338", "display": "8", "app": "cursor"}

    with (
        patch("cursor_bridge.cdp_pages.list_pages", return_value=pages),
        patch(
            "cursor_bridge.bridge_glass_relay.subscription_display_for_gui_host",
            return_value="8",
        ),
        patch("cursor_bridge.bridge_glass_relay.normalize_target", return_value=target),
        patch(
            "cursor_bridge.bridge_glass_relay.asyncio.run",
            return_value={"ok": True, "reason": "delivered"},
        ),
        patch("cursor_bridge.spawn_endpoint_house_ledger.record_tab_wake"),
    ):
        out = relay_wake_tab(
            gui_host=":8",
            agent_id=None,
            body="BRIDGE_WAKE thread=21220 turn=1",
            thread="21220",
        )
    assert out.get("ok") is True
    assert out.get("gui_host") == ":8"


def test_relay_wake_tab_agent_id_no_single_page_fallback() -> None:
    from cursor_bridge.bridge_glass_relay import relay_wake_tab

    pages = [{"title": "Random", "web_socket_debugger_url": "ws://only/1"}]
    target = {"ok": True, "origin": "http://127.0.0.1:9338", "display": "8", "app": "cursor"}
    with (
        patch("cursor_bridge.cdp_pages.list_pages", return_value=pages),
        patch(
            "cursor_bridge.bridge_glass_relay.subscription_display_for_gui_host",
            return_value="8",
        ),
        patch("cursor_bridge.bridge_glass_relay.normalize_target", return_value=target),
    ):
        out = relay_wake_tab(
            gui_host=":8",
            agent_id="723224a2-ea2a-5b32-a19c-70e2ae09430e",
            body="wake",
        )
    assert out["ok"] is False
    assert out["error"] == "no_page"
