"""spawn_tab / spawn_house must refuse Grok gui hosts (:9, :10)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from cursor_bridge.bridge_spawn_relay import relay_spawn_house, relay_spawn_tab

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("gui_host", [":9", ":10", "9", "10", "jupiter"])
def test_relay_spawn_tab_unknown_gui_host(gui_host: str) -> None:
    out = relay_spawn_tab(gui_host=gui_host, prompt="go", model="composer")
    assert out["ok"] is False
    assert out["code"] == "unknown_gui_host"
    assert out["op"] == "spawn_tab"
    assert out["gui_host"] == gui_host


@pytest.mark.parametrize("gui_host", [":9", ":10"])
def test_relay_spawn_house_unknown_gui_host(gui_host: str) -> None:
    out = relay_spawn_house(
        gui_host=gui_host,
        slug="s",
        prompt="go",
        model="composer",
    )
    assert out["ok"] is False
    assert out["code"] == "unknown_gui_host"
    assert out["op"] == "spawn_house"


def test_spawn_tab_valid_host_uses_container_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"172.18.0.5"}),
    )
    with patch("cursor_bridge.bridge_spawn_relay.launch_cursor_agents_tab") as launch:
        launch.return_value = {"ok": True}
        out = relay_spawn_tab(gui_host=":8", prompt="go", model="composer")
    assert out["ok"] is True
    assert launch.call_args.kwargs["debug_url"] == "http://172.18.0.1:9338"
