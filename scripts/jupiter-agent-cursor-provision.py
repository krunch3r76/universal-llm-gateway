#!/usr/bin/env python3
"""Install Jupiter COSMIC Glass: debug CDP + Cosmic application menu launcher.

Runs on **jupiter** (fleet graphical host). Cursor listens on loopback
``19338`` with profile ``~/.gateway/cursor-jupiter``. From **io**, automation
targets ``gui_host=jupiter`` at ``http://127.0.0.1:19339`` (see
``config/cursor-windows.json``) — forward hub loopback 19339 → jupiter:19338
via personal autossh (``scripts.local/``), same pattern as orion-node.

Provides:
  - ``cursor-glass-jupiter.desktop`` for the Cosmic application picker
  - optional ``jupiter-agent-cursor.service`` (same flags; use desktop or unit)

Does not open SSH tunnels from io.
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

_SLOT = "cursor-jupiter"
_SUBSCRIPTION_DISPLAY = "jupiter"
# Cosmic Xwayland for house BROWSER / xdg-open children (not the subscription name).
_X11_DISPLAY = ":1"
_CDP_PORT = 19338
_HUB_TUNNEL_PORT = 19339
_UNIT_NAME = "jupiter-agent-cursor.service"
_DESKTOP_NAME = "cursor-glass-jupiter.desktop"


def render_cursor_unit_body(*, home: Path) -> str:
    profile = home / ".gateway" / "cursor-jupiter"
    cursor_bin = runtime_cursor_bin(home)
    env_block = format_systemd_environment_block(
        display_browser_environment(_SLOT, _X11_DISPLAY, home=home)
    )
    return f"""[Unit]
Description=Cursor Glass on jupiter (local CDP {_CDP_PORT}; io hub {_HUB_TUNNEL_PORT})
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
    """Cosmic menu entry — use this instead of stock Cursor for Glass CDP."""
    profile = home / ".gateway" / "cursor-jupiter"
    cursor_bin = runtime_cursor_bin(home)
    exec_line = (
        f"{cursor_bin} --user-data-dir={profile} "
        f"--remote-debugging-port={_CDP_PORT} --remote-allow-origins=* %F"
    )
    return f"""[Desktop Entry]
Name=Cursor Glass (Jupiter debug)
Comment=Attended Glass on jupiter — local CDP {_CDP_PORT}; io hub {_HUB_TUNNEL_PORT}
GenericName=Text Editor
Exec={exec_line}
Icon=co.anysphere.cursor
Type=Application
StartupNotify=false
StartupWMClass=Cursor
Categories=TextEditor;Development;IDE;
MimeType=application/x-cursor-workspace;
Actions=new-empty-window;
Keywords=cursor;glass;jupiter;debug;

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


def provision_jupiter_glass(
    *,
    home: Path,
    apply: bool,
    autostart: bool,
    daemon_reload: Callable[[], object] | None = None,
) -> dict[str, object]:
    runtime = ensure_cursor_runtime(home=home, apply=apply)
    unit_rendered = render_cursor_unit_body(home=home)
    desktop_rendered = render_desktop_entry(home=home)
    unit_path = _systemd_user_dir(home) / _UNIT_NAME
    desktop_path = _applications_dir(home) / _DESKTOP_NAME
    autostart_path = _autostart_dir(home) / _DESKTOP_NAME

    installed_unit = unit_path.read_text(encoding="utf-8") if unit_path.is_file() else None
    installed_desktop = (
        desktop_path.read_text(encoding="utf-8") if desktop_path.is_file() else None
    )

    print(f"Plan: {_DESKTOP_NAME} (Cosmic application picker)", file=sys.stderr)
    desktop_diff = _plan_diff(installed_desktop, desktop_rendered, desktop_path)
    print(desktop_diff if desktop_diff.strip() else "(no desktop drift)", file=sys.stderr)
    print(f"Plan: {_UNIT_NAME} (optional)", file=sys.stderr)
    unit_diff = _plan_diff(installed_unit, unit_rendered, unit_path)
    print(unit_diff if unit_diff.strip() else "(no unit drift)", file=sys.stderr)
    if autostart:
        print(f"Plan: autostart symlink → {autostart_path}", file=sys.stderr)

    shim: dict[str, object] = {}
    reload_ran = False
    if apply:
        shim = provision_display_browser(_SLOT, home=home)
        _applications_dir(home).mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(desktop_rendered, encoding="utf-8")
        _systemd_user_dir(home).mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_rendered, encoding="utf-8")
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

    verify_local = f"curl -sf http://127.0.0.1:{_CDP_PORT}/json/version"
    print(
        f"On jupiter: launch from Cosmic → “Cursor Glass (Jupiter debug)”, then: {verify_local}",
        file=sys.stderr,
    )
    print(
        f"On io: tunnel jupiter:{_CDP_PORT} → 127.0.0.1:{_HUB_TUNNEL_PORT}, "
        f"then: curl -sf http://127.0.0.1:{_HUB_TUNNEL_PORT}/json/version",
        file=sys.stderr,
    )

    return {
        "ok": True,
        "slot": _SLOT,
        "subscription_display": _SUBSCRIPTION_DISPLAY,
        "local_cdp_port": _CDP_PORT,
        "hub_tunnel_local_port": _HUB_TUNNEL_PORT,
        "desktop_path": str(desktop_path),
        "unit_path": str(unit_path),
        "unit_changed": installed_unit != unit_rendered,
        "desktop_changed": installed_desktop != desktop_rendered,
        "autostart": autostart,
        "daemon_reload": reload_ran,
        "browser_shim": shim,
        "cursor_runtime": runtime,
        "verify_local_command": verify_local,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write desktop entry, optional unit, and browser shim",
    )
    p.add_argument(
        "--autostart",
        action="store_true",
        help="With --apply, symlink desktop into ~/.config/autostart/",
    )
    return p


def main() -> int:
    args = _parser().parse_args()
    result = provision_jupiter_glass(
        home=Path.home(), apply=args.apply, autostart=args.autostart
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
