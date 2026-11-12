"""Host sand-data artifact installation tests."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from grokbot_box_mcp.provision import provision_box_mcp_artifacts
from grokbot_box_mcp.window_units import load_window_profile


@pytest.mark.offline
def test_provision_installs_launcher_and_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    profile = load_window_profile("gc2", home=home)
    profile.user_data_dir.mkdir(parents=True)
    out = provision_box_mcp_artifacts(profile, token="seed-token")
    launcher = Path(out["launcher_host_path"])
    header = Path(out["header_host_path"])
    assert launcher.is_file()
    assert launcher.stat().st_mode & stat.S_IXUSR
    assert header.is_file()
    assert "Bearer seed-token" in header.read_text(encoding="utf-8")
