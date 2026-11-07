"""spawn_tab / spawn_house must refuse Grok gui hosts (:9, :10)."""

from __future__ import annotations

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
