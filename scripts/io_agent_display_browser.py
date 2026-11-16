"""Slot-agnostic display-browser shim install and systemd Environment= blocks."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_REPO_SCRIPT = Path(__file__).resolve().parent / "io-agent" / "io-agent-open-url.sh"

_PATH_TAIL = (
    ".local/bin",
    "bin",
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/snap/bin",
)


def _install_executable(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    dest.chmod(mode)


def provision_display_browser(slot: str, *, home: Path | None = None) -> dict[str, object]:
    """Install io-agent-open-url.sh and an xdg-open copy under ~/.config/io-agent-<slot>/bin."""
    home = home or Path.home()
    bin_dir = home / ".config" / f"io-agent-{slot}" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    open_url = bin_dir / "io-agent-open-url.sh"
    xdg_open = bin_dir / "xdg-open"
    _install_executable(_REPO_SCRIPT, open_url)
    shutil.copy2(open_url, xdg_open)
    xdg_open.chmod(open_url.stat().st_mode)
    return {
        "ok": True,
        "slot": slot,
        "bin_dir": str(bin_dir),
        "open_url": str(open_url),
        "xdg_open": str(xdg_open),
    }


def display_browser_environment(
    slot: str, display: str, *, home: Path | None = None
) -> dict[str, str]:
    """Environment keys for a house unit: PATH-first shim, BROWSER, and io-agent URL route vars."""
    home = home or Path.home()
    bin_dir = home / ".config" / f"io-agent-{slot}" / "bin"
    open_url = bin_dir / "io-agent-open-url.sh"
    browser_root = home / ".gateway" / f"{slot}-io-browser"
    path_parts = [str(bin_dir)] + [str(home / part) if not part.startswith("/") else part for part in _PATH_TAIL]
    return {
        "PATH": os.pathsep.join(path_parts),
        "BROWSER": str(open_url),
        "IO_AGENT_DISPLAY": display,
        "IO_AGENT_BROWSER_PROFILE": str(browser_root / "chrome-auth-profile"),
        "IO_AGENT_BROWSER_CLASS": f"io-agent-browser-{slot}",
        "IO_AGENT_OPEN_URL_LOG": str(browser_root / "open-url.log"),
    }


def format_systemd_environment_block(env: dict[str, str]) -> str:
    """Render systemd Environment= lines with stable key ordering."""
    lines = [f"Environment={key}={env[key]}" for key in sorted(env)]
    return "\n".join(lines)
