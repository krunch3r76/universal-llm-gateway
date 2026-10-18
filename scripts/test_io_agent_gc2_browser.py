"""Tests for gc2 browser wrapper provisioning."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from io_agent_gc2_browser import format_systemd_environment_block, gc2_service_environment, provision_gc2_browser_config


@pytest.mark.offline
def test_provision_writes_executable_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    out = provision_gc2_browser_config(home=home)
    open_url = Path(out["open_url"])
    assert open_url.is_file()
    assert open_url.stat().st_mode & stat.S_IXUSR
    mime = Path(out["mimeapps"])
    assert "grok-bot-gc2.desktop" in mime.read_text(encoding="utf-8")


@pytest.mark.offline
def test_gc2_open_url_launches_chrome_with_profile(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parent
    script = repo / "io-agent" / "gc2-open-url.sh"
    fake_chrome = tmp_path / "chrome"
    log = tmp_path / "chrome-invocations.log"
    fake_chrome.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n", encoding="utf-8")
    fake_chrome.chmod(fake_chrome.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "DISPLAY": ":10",
        "GC2_CHROME_BIN": str(fake_chrome),
        "GC2_CHROME_PROFILE": str(tmp_path / "profile"),
        "GC2_DATA_DIR": str(tmp_path / "grok-data"),
    }
    proc = subprocess.run(
        ["bash", str(script), "https://authenticator.cursor.sh/example"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 0
    assert (tmp_path / "profile").is_dir()
    for _ in range(20):
        if log.is_file():
            break
        time.sleep(0.05)
    logged = log.read_text(encoding="utf-8")
    assert "--user-data-dir=" in logged
    assert "https://authenticator.cursor.sh/example" in logged


@pytest.mark.offline
def test_systemd_environment_includes_prepend_path() -> None:
    env = gc2_service_environment(home=Path("/home/io"))
    block = format_systemd_environment_block(env)
    assert "Environment=PATH=/home/io/.config/io-agent-gc2/bin:" in block
    assert "Environment=BROWSER=/home/io/.config/io-agent-gc2/bin/gc2-open-url.sh" in block
    assert "Environment=XDG_CONFIG_DIRS=/home/io/.config/io-agent-gc2:/etc/xdg" in block
