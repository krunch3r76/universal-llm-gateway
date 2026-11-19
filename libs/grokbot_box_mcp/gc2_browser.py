"""Gc2 (:10) OAuth browser shims — Chromium-only, synced from repo on every ensure."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GC2_OPEN_URL_SRC = _REPO_ROOT / "scripts" / "io-agent" / "gc2-open-url.sh"
_DESKTOP_NAMES = (
    "grok-bot-gc2.desktop",
    "gc2-browser-wrapper.desktop",
)


def _install_executable(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    dest.chmod(mode)


def resolve_playwright_chromium_bin() -> Path | None:
    """Newest Playwright chromium under ~/.cache/ms-playwright (or PLAYWRIGHT_BROWSERS_PATH)."""
    cache = Path(
        os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(Path.home() / ".cache" / "ms-playwright"),
        )
    )
    hits = sorted(
        cache.glob("chromium-*/chrome-linux64/chrome"),
        key=lambda p: p.parts[-3] if len(p.parts) >= 3 else "",
    )
    for candidate in reversed(hits):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def gc2_config_dir(*, home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "io-agent-gc2"


def ensure_gc2_browser_shims(*, home: Path | None = None) -> dict[str, object]:
    """Copy gc2-open-url.sh → BROWSER + xdg-open so OAuth never uses stale Firefox fallbacks."""
    if not _GC2_OPEN_URL_SRC.is_file():
        raise FileNotFoundError(f"gc2 open-url source missing: {_GC2_OPEN_URL_SRC}")

    home = home or Path.home()
    config = gc2_config_dir(home=home)
    bin_dir = config / "bin"
    apps = home / ".local" / "share" / "applications"
    bin_dir.mkdir(parents=True, exist_ok=True)
    apps.mkdir(parents=True, exist_ok=True)

    shims = config / "shims"
    shims.mkdir(parents=True, exist_ok=True)
    shim_canonical = shims / "gc2-open-url.sh"
    _install_executable(_GC2_OPEN_URL_SRC, shim_canonical)

    open_url = bin_dir / "gc2-open-url.sh"
    xdg_open = bin_dir / "xdg-open"
    _install_executable(shim_canonical, open_url)
    shutil.copy2(open_url, xdg_open)
    xdg_open.chmod(open_url.stat().st_mode)

    mime_src = _REPO_ROOT / "scripts" / "io-agent" / "gc2-mimeapps.list"
    if mime_src.is_file():
        shutil.copy2(mime_src, config / "mimeapps.list")

    for name in _DESKTOP_NAMES:
        desktop_src = _REPO_ROOT / "scripts" / "io-agent" / name
        if desktop_src.is_file():
            shutil.copy2(desktop_src, apps / name)

    ensure_script = bin_dir / "ensure-browser-shim.sh"
    ensure_script.write_text(
        f"""#!/usr/bin/env bash
# Sync gc2 OAuth helpers from last provisioned shim (no Firefox fallback).
set -euo pipefail
CFG={config!s}
SRC="$CFG/shims/gc2-open-url.sh"
OPEN="$CFG/bin/gc2-open-url.sh"
if [[ ! -f "$SRC" ]]; then
  echo "ensure-browser-shim: missing $SRC — run provision-gc2" >&2
  exit 1
fi
install -m 0755 "$SRC" "$OPEN"
cp "$OPEN" "$CFG/bin/xdg-open"
if grep -v '^[[:space:]]*#' "$CFG/bin/xdg-open" | grep -Eiq '(^|[[:space:]])(/usr/bin/)?firefox[[:space:]]'; then
  echo "ensure-browser-shim: refusing firefox fallback in xdg-open" >&2
  exit 1
fi
""",
        encoding="utf-8",
    )
    ensure_script.chmod(ensure_script.stat().st_mode | stat.S_IXUSR)

    chrome = resolve_playwright_chromium_bin()
    mimeapps = config / "mimeapps.list"
    return {
        "ok": True,
        "config_dir": str(config),
        "bin_dir": str(bin_dir),
        "open_url": str(open_url),
        "xdg_open": str(xdg_open),
        "mimeapps": str(mimeapps),
        "ensure_script": str(ensure_script),
        "chromium_bin": str(chrome) if chrome else None,
        "chromium_ready": chrome is not None,
        "applications": [str(apps / n) for n in _DESKTOP_NAMES],
    }


def gc2_service_environment(*, home: Path | None = None) -> dict[str, str]:
    """systemd Environment= keys for io-agent-grok-gc2 OAuth browser routing."""
    home = home or Path.home()
    config = gc2_config_dir(home=home)
    bin_dir = config / "bin"
    udd = home / ".gateway" / "grok-io-gc2"
    path = os.pathsep.join([str(bin_dir), "/usr/local/bin", "/usr/bin", "/bin"])
    uid = os.getuid()
    return {
        "DISPLAY": ":10",
        "GROK_AGENT_DATA_DIR": str(udd),
        "XDG_CONFIG_HOME": str(udd / ".config"),
        "XDG_CONFIG_DIRS": f"{config}:/etc/xdg",
        "PATH": path,
        "BROWSER": str(bin_dir / "gc2-open-url.sh"),
        "MOZ_DISABLE_REMOTE": "1",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus-display-10",
    }