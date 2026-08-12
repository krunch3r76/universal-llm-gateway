"""Fail-closed host-targeting refusal under cursor-sdk dispatch HOME."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import is_dispatch_home_path
from services.git_integration_worker.dispatch_home_host_guard import (
    DispatchHomeHostRefusal,
    host_target_refusal_message,
    refuse_host_target_if_dispatch_home,
)


def test_refuse_when_home_is_dispatch_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    overlay = dispatch_root / "auto-deadbeef-home"
    overlay.mkdir(parents=True)
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    monkeypatch.setenv("HOME", str(overlay))

    with pytest.raises(DispatchHomeHostRefusal) as exc_info:
        refuse_host_target_if_dispatch_home(tool="fixture-tool")
    msg = str(exc_info.value)
    assert "REFUSED" in msg
    assert "fixture-tool" in msg
    assert str(overlay) in msg
    assert "HOME=" in msg


def test_pass_when_home_is_passwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.git_integration_worker.cursor_home as home_mod

    passwd = home_mod._passwd_home()
    monkeypatch.setenv("HOME", str(passwd))
    refuse_host_target_if_dispatch_home(tool="fixture-tool")


def test_cli_refuses_under_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    overlay = dispatch_root / "auto-cli-home"
    overlay.mkdir(parents=True)
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    monkeypatch.setenv("HOME", str(overlay))

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.git_integration_worker.dispatch_home_host_guard",
            "install-ecosystem-plugin.sh",
        ],
        check=False,
        text=True,
        capture_output=True,
        env={
            **dict(__import__("os").environ),
            "HOME": str(overlay),
            "CURSOR_DISPATCH_HOME_ROOT": str(dispatch_root),
        },
    )
    assert proc.returncode == 1
    assert "REFUSED" in proc.stderr
    assert "install-ecosystem-plugin.sh" in proc.stderr


def test_install_script_refuses_under_live_dispatch_home() -> None:
    """AC2 falsifier: install under dispatch HOME must exit != 0."""
    import os

    home = os.environ.get("HOME", "")
    if not is_dispatch_home_path(home):
        pytest.skip("not running under a live dispatch HOME")

    repo = Path(__file__).resolve().parents[3]
    script = repo / "scripts" / "cursor" / "install-ecosystem-plugin.sh"
    host_plugins = Path("/home/io/.cursor/plugins/local/ulg-ecosystem")

    before = subprocess.run(
        ["find", str(host_plugins), "-type", "f", "-exec", "sha256sum", "{}", "+"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert before.returncode == 0, before.stderr

    proc = subprocess.run(
        ["bash", str(script), "--dry-run"],
        check=False,
        text=True,
        capture_output=True,
        cwd=str(repo),
    )
    assert proc.returncode != 0, proc.stdout
    assert "REFUSED" in proc.stderr

    after = subprocess.run(
        ["find", str(host_plugins), "-type", "f", "-exec", "sha256sum", "{}", "+"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert after.returncode == 0
    assert before.stdout == after.stdout


def test_message_names_operator_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    overlay = dispatch_root / "auto-msg-home"
    overlay.mkdir(parents=True)
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    msg = host_target_refusal_message(tool="t", overlay_home=overlay)
    assert str(home_mod._passwd_home()) in msg
