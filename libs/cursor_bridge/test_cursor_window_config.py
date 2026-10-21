"""Offline tests for config/cursor-windows.json loader."""

from __future__ import annotations

import pytest

from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    is_cursor_xvfb_host,
)
from cursor_bridge.cursor_window_config import (
    config_path,
    load_config,
    subscription_display_for_gui_host,
)

pytestmark = pytest.mark.offline


def test_cursor_windows_config_tracked_and_loads() -> None:
    assert config_path().is_file()
    cfg = load_config()
    assert cfg["schema_version"] == 1
    assert ":8" in cfg["windows"]
    assert "orion-node" in cfg["windows"]


@pytest.mark.parametrize(
    "host",
    [
        ":8",
        "8",
        "io:8",
        "127.0.0.1:9338",
        "http://127.0.0.1:9338",
        "orion-node",
        "http://127.0.0.1:19338",
    ],
)
def test_configured_hosts_match_prior_behavior(host: str) -> None:
    assert is_cursor_xvfb_host(host) is True
    assert is_cursor_xvfb_host("jupiter") is False


def test_cdp_origins_from_config() -> None:
    assert cursor_debug_url_for_gui_host("orion-node") == "http://127.0.0.1:19338"
    assert cursor_debug_url_for_gui_host(":8") == "http://127.0.0.1:9338"


def test_cdp_origin_bridge_client_rewrites_display_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"172.18.0.5"}),
    )
    assert cursor_debug_url_for_gui_host(":8") == "http://172.18.0.1:9338"
    assert cursor_debug_url_for_gui_host("orion-node") == "http://127.0.0.1:19338"


def test_cdp_origin_host_gateway_stays_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"127.0.0.1", "172.18.0.1"}),
    )
    assert cursor_debug_url_for_gui_host(":8") == "http://127.0.0.1:9338"


def test_subscription_display_from_config() -> None:
    assert subscription_display_for_gui_host("orion-node") == "orion-node"
    assert subscription_display_for_gui_host(":8") == "8"
