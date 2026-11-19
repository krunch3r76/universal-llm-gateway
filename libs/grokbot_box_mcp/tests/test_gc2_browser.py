"""Gc2 OAuth browser shim provisioning."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from grokbot_box_mcp.gc2_browser import ensure_gc2_browser_shims
from grokbot_box_mcp.window_units import grok_systemd_unit_body, load_window_profile


@pytest.mark.offline
def test_ensure_syncs_xdg_open_with_gc2_open_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    out = ensure_gc2_browser_shims(home=home)
    open_url = Path(out["open_url"])
    xdg = Path(out["xdg_open"])
    assert open_url.read_bytes() == xdg.read_bytes()
    assert "firefox" not in open_url.read_text(encoding="utf-8").lower() or (
        "avoid firefox" in open_url.read_text(encoding="utf-8").lower()
    )
    assert open_url.stat().st_mode & stat.S_IXUSR
    ensure = Path(out["ensure_script"])
    assert ensure.is_file()


@pytest.mark.offline
def test_grok_systemd_unit_includes_exec_start_pre_for_gc2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".venvs" / "universal" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    ensure = home / ".config/io-agent-gc2/bin/ensure-browser-shim.sh"
    ensure.parent.mkdir(parents=True)
    ensure.write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    profile = load_window_profile("gc2", home=home)
    body = grok_systemd_unit_body(profile, home=home, env_block="Environment=DISPLAY=:10")
    assert "ExecStartPre=" in body
    assert "ensure-browser-shim.sh" in body


@pytest.mark.offline
def test_grok_systemd_unit_omits_exec_start_pre_for_gc1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    profile = load_window_profile("gc1", home=home)
    body = grok_systemd_unit_body(profile, home=home, env_block="Environment=DISPLAY=:9")
    assert "ExecStartPre=" not in body