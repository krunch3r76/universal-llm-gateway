"""Tests for parameterised Grok window systemd/env (gc1/gc2 parity)."""

from __future__ import annotations

import pytest
from grokbot_box_mcp.window_units import load_window_profile
from io_agent_grok_window import grok_service_environment, systemd_grok_bundle


@pytest.mark.offline
def test_gc2_unit_bundle_matches_live_env_keys(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    profile = load_window_profile("gc2", home=home)
    env = grok_service_environment(profile, home=home)
    assert env["GROK_AGENT_DATA_DIR"] == str(profile.user_data_dir)
    assert env["XDG_CONFIG_HOME"] == str(profile.user_data_dir / ".config")
    assert env["DISPLAY"] == ":10"
    assert "DBUS_SESSION_BUS_ADDRESS" in env
    units = systemd_grok_bundle("gc2", home=home)
    body = units[profile.grok_unit]
    assert "io-agent-portal-10.service" in body
    assert str(profile.user_data_dir) in body
