#!/usr/bin/env python3
"""Provision io-agent Xvfb :13 + VNC 5913 + Chromium (CDP 9343) for Claude.

Mirrors ``scripts/io-agent-chatgpt-provision.py``. One Chromium, one profile,
two start URLs so claude.ai and the console share a login. CDP URL for this
house is ``http://127.0.0.1:9343``. ``WEB_TAB_CDP_URL`` is unchanged; callers
pass this URL explicitly.

Re-running writes only the three Claude units. It does not touch the ChatGPT
units. ``--start`` enables those three units; omit it when they are already
active so the live browser is not restarted.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SYSTEMD_USER_DIR = Path.home() / ".config/systemd/user"
_PROFILE_DIR = Path.home() / ".gateway/claude-ai-io"
_DISPLAY = ":13"
_CDP_PORT = 9343
_VNC_PORT = 5913
_CDP_URL = f"http://127.0.0.1:{_CDP_PORT}"


def _xvfb_13_unit(*, home: Path) -> str:
    return f"""[Unit]
Description=Agent Xvfb :13 (Claude trAId house) 1920x1200
After=network.target

[Service]
EnvironmentFile={home}/.config/io-agent-display.env
ExecStart=/usr/bin/Xvfb :13 -screen 0 ${{IO_AGENT_GEOMETRY}} -s 0 -ac -nolisten tcp
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


_VNC_5913_UNIT = """[Unit]
Description=x11vnc for agent display :13 (Claude, port 5913)
After=io-agent-xvfb-13.service
Requires=io-agent-xvfb-13.service

[Service]
ExecStart=/usr/bin/x11vnc -display :13 -localhost -no6 -rfbport 5913 -nopw -shared -forever -noxdamage -skip_lockkeys -clear_all
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def _resolve_chrome_bin() -> Path:
    override = os.environ.get("IO_AGENT_CHROME_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise SystemExit(f"IO_AGENT_CHROME_BIN not found: {path}")
        return path
    cache = Path.home() / ".cache/ms-playwright"
    matches = sorted(cache.glob("chromium-*/chrome-linux64/chrome"))
    if matches:
        return matches[-1]
    raise SystemExit(
        "No Chromium found. Install Playwright browser: "
        "$HOME/.venvs/universal/bin/python -m playwright install chromium "
        "or set IO_AGENT_CHROME_BIN."
    )


def _claude_chrome_unit_body(*, home: Path, chrome: Path) -> str:
    profile = home / ".gateway/claude-ai-io"
    maximize = home / ".config/io-agent-maximize.py"
    return f"""[Unit]
Description=Claude web house on display :13 (CDP {_CDP_PORT})
After=io-agent-xvfb-13.service
Requires=io-agent-xvfb-13.service

[Service]
Environment=DISPLAY=:13
ExecStart={chrome} --remote-debugging-port={_CDP_PORT} --remote-allow-origins=* \\
  --user-data-dir={profile} --ozone-platform=x11 --no-first-run \\
  --no-default-browser-check --window-size=1920,1200 --window-position=0,0 \\
  --disable-background-timer-throttling \\
  https://claude.ai/ https://console.anthropic.com/
ExecStartPost={home}/.venvs/universal/bin/python {maximize} :13
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def provision(*, start: bool) -> dict[str, object]:
    """Write the three Claude user units. Start them only when asked.

    The house is already an auxiliary ``systemctl --user`` unit, same path as
    ChatGPT. Skipping ``--start`` leaves a running browser on its current
    login page.
    """
    chrome = _resolve_chrome_bin()
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    home = Path.home()
    units = {
        "io-agent-xvfb-13.service": _xvfb_13_unit(home=home),
        "io-agent-vnc-5913.service": _VNC_5913_UNIT,
        "io-agent-claude.service": _claude_chrome_unit_body(home=home, chrome=chrome),
    }
    written: list[str] = []
    for name, body in units.items():
        path = _SYSTEMD_USER_DIR / name
        path.write_text(body, encoding="utf-8")
        written.append(str(path))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    started: list[str] = []
    if start:
        for name in units:
            subprocess.run(["systemctl", "--user", "enable", "--now", name], check=True)
            started.append(name)
    return {
        "ok": True,
        "display": _DISPLAY,
        "cdp_url": _CDP_URL,
        "vnc_local": f"127.0.0.1:{_VNC_PORT}",
        "profile_dir": str(_PROFILE_DIR),
        "chrome_bin": str(chrome),
        "units_written": written,
        "units_started": started,
        "sign_in": (
            "Open VNC 5913 (SSH tunnel), complete Claude login in the browser profile."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--start",
        action="store_true",
        help="Enable and start systemd user units after writing them",
    )
    p.add_argument("--json", action="store_true", help="Print JSON result")
    return p


def main() -> int:
    args = _parser().parse_args()
    result = provision(start=args.start)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
