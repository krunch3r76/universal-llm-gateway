"""Offline tests for slot-agnostic display browser shim and cursor provisioner."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from io_agent_display_browser import (
    display_browser_environment,
    provision_display_browser,
)

_REPO = Path(__file__).resolve().parent
_OPEN_URL = _REPO / "io-agent" / "io-agent-open-url.sh"
_REPO_SCRIPT_TEXT = _OPEN_URL.read_bytes()


def _run_open_url(
    args: list[str],
    *,
    env: dict[str, str],
    script: Path | None = None,
    timeout: float = 5,
) -> subprocess.CompletedProcess[str]:
    runner = script or _OPEN_URL
    return subprocess.run(
        [str(runner), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _base_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    env["HOME"] = str(tmp_path)
    # Hermetic tests: surface poll uses xdotool against a missing X server under offline guards.
    env["IO_AGENT_OPEN_URL_SKIP_SURFACE"] = "1"
    env.update(extra or {})
    return env


@pytest.mark.offline
def test_dry_run_https_house_browser(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    proc = _run_open_url(
        ["https://cursor.com/login"],
        env=_base_env(
            tmp_path,
            {
                "DISPLAY": ":1",
                "WAYLAND_DISPLAY": "wayland-1",
                "IO_AGENT_DISPLAY": ":8",
                "IO_AGENT_BROWSER_PROFILE": str(profile),
                "IO_AGENT_BROWSER_CLASS": "io-agent-browser-cursor",
                "IO_AGENT_OPEN_URL_DRY_RUN": "1",
                "IO_AGENT_CHROME_BIN": str(tmp_path / "chrome"),
            },
        ),
    )
    assert proc.returncode == 0
    assert "route=house-browser" in proc.stdout
    assert "display=:8" in proc.stdout
    assert "wayland_display=unset" in proc.stdout
    assert f"--user-data-dir={profile}" in proc.stdout
    assert "--ozone-platform=x11" in proc.stdout
    assert "--class=io-agent-browser-cursor" in proc.stdout


@pytest.mark.offline
def test_dry_run_delegates_to_later_path_xdg_open(tmp_path: Path) -> None:
    shim_bin = tmp_path / "shim-bin"
    real_bin = tmp_path / "real-bin"
    shim_bin.mkdir()
    real_bin.mkdir()
    stub = real_bin / "xdg-open"
    stub.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    shim_xdg = shim_bin / "xdg-open"
    shim_xdg.write_bytes(_OPEN_URL.read_bytes())
    shim_xdg.chmod(0o755)
    proc = _run_open_url(
        [str(tmp_path / "adir")],
        script=shim_xdg,
        env=_base_env(
            tmp_path,
            {
                "PATH": f"{shim_bin}:{real_bin}:/usr/bin",
                "IO_AGENT_DISPLAY": ":8",
                "IO_AGENT_BROWSER_PROFILE": str(tmp_path / "profile"),
                "IO_AGENT_OPEN_URL_DRY_RUN": "1",
            },
        ),
    )
    assert proc.returncode == 0
    assert "route=delegate-xdg-open" in proc.stdout
    argv_line = proc.stdout.split("argv=", 1)[-1].strip()
    assert str(stub) in argv_line
    assert str(shim_xdg) not in argv_line


@pytest.mark.offline
def test_dry_run_mailto_delegates(tmp_path: Path) -> None:
    real_bin = tmp_path / "real-bin"
    real_bin.mkdir()
    stub = real_bin / "xdg-open"
    stub.write_text("#!/bin/sh\n", encoding="utf-8")
    stub.chmod(0o755)
    shim_bin = tmp_path / "shim-bin"
    shim_bin.mkdir()
    shim_xdg = shim_bin / "xdg-open"
    shim_xdg.write_bytes(_OPEN_URL.read_bytes())
    shim_xdg.chmod(0o755)
    proc = _run_open_url(
        ["mailto:user@example.com"],
        script=shim_xdg,
        env=_base_env(
            tmp_path,
            {
                "PATH": f"{shim_bin}:{real_bin}:/usr/bin",
                "IO_AGENT_DISPLAY": ":8",
                "IO_AGENT_BROWSER_PROFILE": str(tmp_path / "profile"),
                "IO_AGENT_OPEN_URL_DRY_RUN": "1",
            },
        ),
    )
    assert proc.returncode == 0
    assert "route=delegate-xdg-open" in proc.stdout


@pytest.mark.offline
def test_no_chromium_exit_one(tmp_path: Path) -> None:
    env = _base_env(
        tmp_path,
        {
            "IO_AGENT_DISPLAY": ":8",
            "IO_AGENT_BROWSER_PROFILE": str(tmp_path / "profile"),
            "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "empty-pw"),
        },
    )
    env.pop("IO_AGENT_CHROME_BIN", None)
    proc = _run_open_url(
        ["https://example.com"],
        env=env,
    )
    assert proc.returncode == 1
    assert "Playwright Chromium" in proc.stderr


@pytest.mark.offline
def test_no_display_exit_two(tmp_path: Path) -> None:
    env = _base_env(tmp_path, {"IO_AGENT_BROWSER_PROFILE": str(tmp_path / "profile")})
    env.pop("DISPLAY", None)
    proc = _run_open_url(["https://example.com"], env=env)
    assert proc.returncode == 2


@pytest.mark.offline
def test_no_profile_exit_two(tmp_path: Path) -> None:
    proc = _run_open_url(
        ["https://example.com"],
        env=_base_env(tmp_path, {"IO_AGENT_DISPLAY": ":8", "IO_AGENT_CHROME_BIN": "/bin/false"}),
    )
    assert proc.returncode == 2


@pytest.mark.offline
def test_log_strips_query_secret(tmp_path: Path) -> None:
    log = tmp_path / "open-url.log"
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    proc = _run_open_url(
        ["https://cursor.com/loginDeepControl?challenge=SECRET"],
        env=_base_env(
            tmp_path,
            {
                "IO_AGENT_DISPLAY": ":8",
                "IO_AGENT_BROWSER_PROFILE": str(tmp_path / "profile"),
                "IO_AGENT_CHROME_BIN": str(fake),
                "IO_AGENT_OPEN_URL_LOG": str(log),
            },
        ),
    )
    assert proc.returncode == 0
    text = log.read_text(encoding="utf-8")
    assert "cursor.com/loginDeepControl" in text
    assert "SECRET" not in text
    assert "challenge" not in text


@pytest.mark.offline
def test_provision_display_browser_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    first = provision_display_browser("cursor", home=home)
    second = provision_display_browser("cursor", home=home)
    open_url = Path(first["open_url"])
    xdg = Path(first["xdg_open"])
    assert open_url.is_file() and xdg.is_file()
    assert open_url.read_bytes() == _REPO_SCRIPT_TEXT
    assert xdg.read_bytes() == _REPO_SCRIPT_TEXT
    assert open_url.stat().st_mode & stat.S_IXUSR
    assert Path(second["open_url"]).read_bytes() == _REPO_SCRIPT_TEXT
    assert first["bin_dir"] == second["bin_dir"]


@pytest.mark.offline
def test_display_browser_environment_path_and_browser(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    provision_display_browser("cursor", home=home)
    env = display_browser_environment("cursor", ":8", home=home)
    bin_dir = home / ".config" / "io-agent-cursor" / "bin"
    assert env["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert env["BROWSER"] == str(bin_dir / "io-agent-open-url.sh")


def _load_cursor_provision():
    spec = importlib.util.spec_from_file_location(
        "io_agent_cursor_provision",
        _REPO / "io-agent-cursor-provision.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.offline
def test_rendered_cursor_unit_shape(tmp_path: Path) -> None:
    mod = _load_cursor_provision()
    home = Path("/home/io")
    body = mod.render_cursor_unit_body(home=home)
    for needle in (
        "UnsetEnvironment=WAYLAND_DISPLAY",
        "UnsetEnvironment=COSMIC_RENDER_DEVICE",
        "Environment=DISPLAY=:8",
        "Environment=LIBGL_ALWAYS_SOFTWARE=1",
        "ExecStart=/usr/share/cursor/cursor",
        "--user-data-dir=/home/io/.gateway/cursor-io",
        "--remote-debugging-port=9338",
        "ExecStartPost=/home/io/.venvs/universal/bin/python /home/io/.config/io-agent-maximize.py :8",
        "Restart=always",
        "RestartSec=3",
        "Environment=IO_AGENT_DISPLAY=:8",
        "Environment=BROWSER=/home/io/.config/io-agent-cursor/bin/io-agent-open-url.sh",
    ):
        assert needle in body
    for forbidden in (
        "XDG_CURRENT_DESKTOP",
        "XDG_CONFIG_HOME",
        "XDG_CONFIG_DIRS",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        assert forbidden not in body


@pytest.mark.offline
def test_cursor_provision_dry_run_no_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_cursor_provision()
    result = mod.provision_cursor(home=home, apply=False)
    assert result["unit_changed"] is True
    assert not list(home.rglob("*"))
    capsys.readouterr()


@pytest.mark.offline
def test_cursor_provision_apply_unit_changed_flag(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    mod = _load_cursor_provision()
    reloaded: list[str] = []

    def _reload() -> None:
        reloaded.append("yes")

    first = mod.provision_cursor(home=home, apply=True, daemon_reload=_reload)
    assert first["unit_changed"] is True
    assert reloaded == ["yes"]
    assert (home / ".config/io-agent-cursor/bin/io-agent-open-url.sh").is_file()
    second = mod.provision_cursor(home=home, apply=True, daemon_reload=_reload)
    assert second["unit_changed"] is False
