"""Tests for parameterised Grok window systemd/env (gc1/gc2 parity)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from grokbot_box_mcp.window_units import load_window_profile
from io_agent_grok_window import grok_service_environment, systemd_grok_bundle

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "libs/grokbot_box_mcp/tests/fixtures/gc1_io-agent-grok.env.json"
)


@pytest.mark.offline
def test_gc2_unit_bundle_matches_live_env_keys(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


@pytest.mark.offline
def test_gc1_service_environment_matches_live_fixture(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    # Re-root fixture paths onto tmp home.
    fixture["GROK_AGENT_DATA_DIR"] = str(home / ".gateway" / "grok-io")
    fixture["XDG_CONFIG_HOME"] = str(home / ".gateway" / "grok-io" / ".config")
    fixture["BROWSER"] = str(
        home / ".config" / "io-agent-gc1" / "bin" / "gc1-open-url.sh"
    )
    fixture["PATH"] = ":".join(
        [
            str(home / ".config" / "io-agent-gc1" / "bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
    )
    profile = load_window_profile("gc1", home=home)
    env = grok_service_environment(profile, home=home)
    for key, expected in fixture.items():
        assert env[key] == expected
