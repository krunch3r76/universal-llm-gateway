#!/usr/bin/env python3
"""Render and optionally install orion-agent-cursor.service (COSMIC / Wayland Glass).

Dedicated Cursor profile with a stable Chrome DevTools port (19338) for
``gui_host=orion-node`` — same CDP control plane as ``io-agent-cursor`` on Xvfb
``:8`` / 9338. Automation uses ``cursor_bridge`` ``spawn_tab`` / ``wake_tab`` and
``glass_services_js``; it does not use SSH uinput or IDE Ctrl+T.

When the graphical session lives on orion-node and MCP runs on ``io``, something
must forward loopback 19338 on the hub to 19338 on the glass host (personal
``scripts.local/orion/`` autossh is the usual pattern — not installed here).

Also writes ``cursor-glass-orion.desktop`` so Cosmic menu launches always use
the debug flags instead of the stock ``/usr/share/cursor/cursor`` entry.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from cursor_agent_runtime import ensure_cursor_runtime, runtime_cursor_bin
from io_agent_display_browser import (
    display_browser_environment,
    format_systemd_environment_block,
    provision_display_browser,
)

_SLOT = "cursor-orion"
_SUBSCRIPTION_DISPLAY = "orion-node"
# Cosmic Xwayland for house BROWSER / xdg-open children (not the subscription name).
_X11_DISPLAY = ":1"
_CDP_PORT = 19338
_UNIT_NAME = "orion-agent-cursor.service"
_DESKTOP_NAME = "cursor-glass-orion.desktop"


def render_cursor_unit_body(*, home: Path) -> str:
    """User systemd unit: Wayland Glass with remote debugging on 19338."""
    profile = home / ".gateway" / "cursor-orion"
    cursor_bin = runtime_cursor_bin(home)
    env_block = format_systemd_environment_block(
        display_browser_environment(_SLOT, _X11_DISPLAY, home=home)
    )
    # graphically-session-bound: Cosmic session supplies WAYLAND_DISPLAY and XDG_RUNTIME_DIR.
    return f"""[Unit]
Description=Cursor Glass on {_SUBSCRIPTION_DISPLAY} (CDP {_CDP_PORT})
After=graphical-session-pre.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={cursor_bin} \\
  --user-data-dir={profile} \\
  --remote-debugging-port={_CDP_PORT} \\
  --remote-allow-origins=* \\
  --window-size=1920,1200 --window-position=0,0
{env_block}
Restart=always
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def render_desktop_entry(*, home: Path) -> str:
    """Application launcher that always passes the Glass CDP flags."""
    profile = home / ".gateway" / "cursor-orion"
    cursor_bin = runtime_cursor_bin(home)
    exec_line = (
        f"{cursor_bin} --user-data-dir={profile} "
        f"--remote-debugging-port={_CDP_PORT} --remote-allow-origins=* %F"
    )
    return f"""[Desktop Entry]
Name=Cursor Glass (debug)
Comment=Attended Glass on {_SUBSCRIPTION_DISPLAY} — CDP {_CDP_PORT}
GenericName=Text Editor
Exec={exec_line}
Icon=co.anysphere.cursor
Type=Application
StartupNotify=false
StartupWMClass=Cursor
Categories=TextEditor;Development;IDE;
MimeType=application/x-cursor-workspace;
Actions=new-empty-window;
Keywords=cursor;glass;debug;

[Desktop Action new-empty-window]
Name=New Empty Window
Exec={exec_line} --new-window %F
"""


def _systemd_user_dir(home: Path) -> Path:
    return home / ".config" / "systemd" / "user"


def _applications_dir(home: Path) -> Path:
    return home / ".local" / "share" / "applications"


def _autostart_dir(home: Path) -> Path:
    return home / ".config" / "autostart"


def _plan_diff(installed: str | None, rendered: str, path: Path) -> str:
    if installed is None:
        return f"(new file {path})\n"
    return "".join(
        difflib.unified_diff(
            installed.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def provision_orion_glass(
    *,
    home: Path,
    apply: bool,
    autostart: bool,
    daemon_reload: Callable[[], object] | None = None,
) -> dict[str, object]:
    """Install browser shim, systemd unit, and Cosmic desktop entry (no start/stop)."""
    runtime = ensure_cursor_runtime(home=home, apply=apply)
    unit_rendered = render_cursor_unit_body(home=home)
    desktop_rendered = render_desktop_entry(home=home)
    unit_path = _systemd_user_dir(home) / _UNIT_NAME
    desktop_path = _applications_dir(home) / _DESKTOP_NAME
    autostart_path = _autostart_dir(home) / _DESKTOP_NAME

    installed_unit: str | None = None
    if unit_path.is_file():
        installed_unit = unit_path.read_text(encoding="utf-8")
    installed_desktop: str | None = None
    if desktop_path.is_file():
        installed_desktop = desktop_path.read_text(encoding="utf-8")

    unit_changed = installed_unit != unit_rendered
    desktop_changed = installed_desktop != desktop_rendered

    print(f"Plan: {_UNIT_NAME}", file=sys.stderr)
    unit_diff = _plan_diff(installed_unit, unit_rendered, unit_path)
    print(unit_diff if unit_diff.strip() else "(no unit drift)", file=sys.stderr)
    print(f"Plan: {_DESKTOP_NAME}", file=sys.stderr)
    desktop_diff = _plan_diff(installed_desktop, desktop_rendered, desktop_path)
    print(desktop_diff if desktop_diff.strip() else "(no desktop drift)", file=sys.stderr)
    if autostart:
        print(f"Plan: autostart symlink → {autostart_path}", file=sys.stderr)

    shim: dict[str, object] = {}
    reload_ran = False
    if apply:
        shim = provision_display_browser(_SLOT, home=home)
        _systemd_user_dir(home).mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_rendered, encoding="utf-8")
        _applications_dir(home).mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(desktop_rendered, encoding="utf-8")
        if autostart:
            _autostart_dir(home).mkdir(parents=True, exist_ok=True)
            if autostart_path.exists() or autostart_path.is_symlink():
                autostart_path.unlink()
            autostart_path.symlink_to(desktop_path)
        reload = daemon_reload or (
            lambda: subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        )
        reload()
        reload_ran = True
        subprocess.run(
            ["update-desktop-database", str(_applications_dir(home))],
            check=False,
        )

    restart_cmd = f"systemctl --user restart {_UNIT_NAME}"
    enable_cmd = f"systemctl --user enable --now {_UNIT_NAME}"
    verify_cmd = f"curl -sf http://127.0.0.1:{_CDP_PORT}/json/version"
    print(f"Operator enable (not run here): {enable_cmd}", file=sys.stderr)
    print(f"Operator restart (not run here): {restart_cmd}", file=sys.stderr)
    print(f"Verify CDP (on glass host): {verify_cmd}", file=sys.stderr)

    return {
        "ok": True,
        "slot": _SLOT,
        "subscription_display": _SUBSCRIPTION_DISPLAY,
        "cdp_port": _CDP_PORT,
        "unit_path": str(unit_path),
        "desktop_path": str(desktop_path),
        "unit_changed": unit_changed,
        "desktop_changed": desktop_changed,
        "autostart": autostart,
        "daemon_reload": reload_ran,
        "browser_shim": shim,
        "cursor_runtime": runtime,
        "enable_command": enable_cmd,
        "restart_command": restart_cmd,
        "verify_command": verify_cmd,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write shim, unit, and desktop entry; daemon-reload (does not enable/start)",
    )
    p.add_argument(
        "--autostart",
        action="store_true",
        help="With --apply, symlink the desktop entry into ~/.config/autostart/",
    )
    return p


def main() -> int:
    args = _parser().parse_args()
    home = Path.home()
    result = provision_orion_glass(
        home=home, apply=args.apply, autostart=args.autostart
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
