#!/usr/bin/env python3
"""Render and optionally install io-agent-cursor.service with display-browser URL routing."""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from io_agent_display_browser import (
    display_browser_environment,
    format_systemd_environment_block,
    provision_display_browser,
)

_SLOT = "cursor"
_DISPLAY = ":8"
_CDP_PORT = 9338
_UNIT_NAME = "io-agent-cursor.service"
_UNIT_XVFB = "io-agent-xvfb-8.service"
_CURSOR_BIN = Path("/usr/share/cursor/cursor")


def render_cursor_unit_body(*, home: Path) -> str:
    """Unit body matching live io-agent-cursor.service plus Wayland unset and browser env."""
    profile = home / ".gateway" / "cursor-io"
    maximize = home / ".config" / "io-agent-maximize.py"
    venv_python = home / ".venvs" / "universal" / "bin" / "python"
    env_block = format_systemd_environment_block(
        display_browser_environment(_SLOT, _DISPLAY, home=home)
    )
    return f"""[Unit]
Description=Cursor agent profile on display {_DISPLAY} (CDP {_CDP_PORT})
After={_UNIT_XVFB}
Requires={_UNIT_XVFB}

[Service]
Environment=DISPLAY={_DISPLAY}
# Without this, child browsers follow the session Wayland compositor instead of Xvfb.
UnsetEnvironment=WAYLAND_DISPLAY
# Xvfb has no DRI; cosmic.conf renderD128 breaks Chromium GPU → blank VNC/Glass window.
UnsetEnvironment=COSMIC_RENDER_DEVICE
Environment=LIBGL_ALWAYS_SOFTWARE=1
ExecStart={_CURSOR_BIN} --user-data-dir={profile} --remote-debugging-port={_CDP_PORT} --window-size=1920,1200 --window-position=0,0 --ozone-platform=x11 --disable-gpu --disable-gpu-compositing
ExecStartPost={venv_python} {maximize} {_DISPLAY}
{env_block}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def _systemd_user_dir(home: Path) -> Path:
    return home / ".config" / "systemd" / "user"


def _plan_diff(installed: str | None, rendered: str, unit_path: Path) -> str:
    if installed is None:
        return f"(new file {unit_path})\n"
    return "".join(
        difflib.unified_diff(
            installed.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(unit_path),
            tofile=str(unit_path),
        )
    )


def provision_cursor(
    *, home: Path, apply: bool, daemon_reload: Callable[[], object] | None = None
) -> dict[str, object]:
    """Install browser shim and optionally write the cursor systemd unit (no start/stop)."""
    rendered = render_cursor_unit_body(home=home)
    unit_path = _systemd_user_dir(home) / _UNIT_NAME
    installed_text: str | None = None
    if unit_path.is_file():
        installed_text = unit_path.read_text(encoding="utf-8")

    unit_changed = installed_text != rendered
    shim: dict[str, object] = {}

    diff_text = _plan_diff(installed_text, rendered, unit_path)
    print("Plan: io-agent-cursor.service")
    if diff_text.strip():
        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
    else:
        print("(no unit drift)")

    reload_ran = False
    if apply:
        shim = provision_display_browser(_SLOT, home=home)
        _systemd_user_dir(home).mkdir(parents=True, exist_ok=True)
        unit_path.write_text(rendered, encoding="utf-8")
        reload = daemon_reload or (lambda: subprocess.run(["systemctl", "--user", "daemon-reload"], check=True))
        reload()
        reload_ran = True

    restart_cmd = f"systemctl --user restart {_UNIT_NAME}"
    print(f"Operator restart (not run here): {restart_cmd}")

    return {
        "ok": True,
        "slot": _SLOT,
        "display": _DISPLAY,
        "unit_path": str(unit_path),
        "unit_changed": unit_changed,
        "daemon_reload": reload_ran,
        "browser_shim": shim,
        "restart_command": restart_cmd,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write shim and unit; run systemctl --user daemon-reload (does not restart the unit)",
    )
    return p


def main() -> int:
    args = _parser().parse_args()
    home = Path.home()
    result = provision_cursor(home=home, apply=args.apply)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
