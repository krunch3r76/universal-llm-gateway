"""Install gc2 (:10) browser / xdg-open wrappers for Grok Bot OAuth on io."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_REPO_SCRIPTS = Path(__file__).resolve().parent / "io-agent"
_GC2_CONFIG = Path.home() / ".config" / "io-agent-gc2"
_GC2_BIN = _GC2_CONFIG / "bin"
_GC2_APPS = Path.home() / ".local" / "share" / "applications"


def _install_executable(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    dest.chmod(mode)


def provision_gc2_browser_config(*, home: Path | None = None) -> dict[str, object]:
    """Write gc2 browser wrappers and desktop entries under ``~/.config/io-agent-gc2``."""
    home = home or Path.home()
    config = home / ".config" / "io-agent-gc2"
    bin_dir = config / "bin"
    apps = home / ".local" / "share" / "applications"

    bin_dir.mkdir(parents=True, exist_ok=True)
    apps.mkdir(parents=True, exist_ok=True)

    open_url = bin_dir / "gc2-open-url.sh"
    xdg_open = bin_dir / "xdg-open"
    _install_executable(_REPO_SCRIPTS / "gc2-open-url.sh", open_url)
    shutil.copy2(open_url, xdg_open)

    for name in (
        "grok-bot-gc2.desktop",
        "gc2-browser-wrapper.desktop",
    ):
        shutil.copy2(_REPO_SCRIPTS / name, apps / name)

    mimeapps = config / "mimeapps.list"
    shutil.copy2(_REPO_SCRIPTS / "gc2-mimeapps.list", mimeapps)

    return {
        "ok": True,
        "config_dir": str(config),
        "bin_dir": str(bin_dir),
        "open_url": str(open_url),
        "mimeapps": str(mimeapps),
        "applications": [str(apps / "grok-bot-gc2.desktop"), str(apps / "gc2-browser-wrapper.desktop")],
    }


def gc2_service_environment(*, home: Path | None = None) -> dict[str, str]:
    """systemd Environment= keys for io-agent-grok-gc2 OAuth browser routing."""
    home = home or Path.home()
    config = home / ".config" / "io-agent-gc2"
    bin_dir = config / "bin"
    path = os.pathsep.join([str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"])
    return {
        "DISPLAY": ":10",
        "XDG_CONFIG_DIRS": f"{config}:/etc/xdg",
        "PATH": path,
        "BROWSER": str(bin_dir / "gc2-open-url.sh"),
        "MOZ_DISABLE_REMOTE": "1",
    }


def format_systemd_environment_block(env: dict[str, str]) -> str:
    lines = []
    for key in sorted(env):
        lines.append(f"Environment={key}={env[key]}")
    return "\n".join(lines)
