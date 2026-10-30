#!/usr/bin/env python3
"""Provision io-agent Xvfb :12 + VNC 5912 + Chromium (CDP 9342) for chatgpt.com.

Pattern: scripts/grok-gc1-gc2-relay.py provision-gc2. Chrome binary defaults to
Playwright's bundled Chromium on io (no system google-chrome required).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SYSTEMD_USER_DIR = Path.home() / ".config/systemd/user"
_PROFILE_DIR = Path.home() / ".gateway/chatgpt-io"
_DISPLAY = ":12"
_CDP_PORT = 9342
_VNC_PORT = 5912

def _xvfb_12_unit(*, home: Path) -> str:
    return f"""[Unit]
Description=Agent Xvfb :12 (ChatGPT trAId house) 1920x1200
After=network.target

[Service]
EnvironmentFile={home}/.config/io-agent-display.env
ExecStart=/usr/bin/Xvfb :12 -screen 0 ${{IO_AGENT_GEOMETRY}} -s 0 -ac -nolisten tcp
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""

_VNC_5912_UNIT = """[Unit]
Description=x11vnc for agent display :12 (ChatGPT, port 5912)
After=io-agent-xvfb-12.service
Requires=io-agent-xvfb-12.service

[Service]
ExecStart=/usr/bin/x11vnc -display :12 -localhost -no6 -rfbport 5912 -nopw -shared -forever -noxdamage
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


def _chatgpt_chrome_unit_body(*, home: Path, chrome: Path) -> str:
    profile = home / ".gateway/chatgpt-io"
    maximize = home / ".config/io-agent-maximize.py"
    return f"""[Unit]
Description=ChatGPT web house on display :12 (CDP {_CDP_PORT})
After=io-agent-xvfb-12.service
Requires=io-agent-xvfb-12.service

[Service]
Environment=DISPLAY=:12
ExecStart={chrome} --remote-debugging-port={_CDP_PORT} --remote-allow-origins=* \\
  --user-data-dir={profile} --ozone-platform=x11 --no-first-run \\
  --no-default-browser-check --window-size=1920,1200 --window-position=0,0 \\
  --disable-background-timer-throttling \\
  https://chatgpt.com/
ExecStartPost={home}/.venvs/universal/bin/python {maximize} :12
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def provision(*, start: bool) -> dict[str, object]:
    chrome = _resolve_chrome_bin()
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    home = Path.home()
    units = {
        "io-agent-xvfb-12.service": _xvfb_12_unit(home=home),
        "io-agent-vnc-5912.service": _VNC_5912_UNIT,
        "io-agent-chatgpt.service": _chatgpt_chrome_unit_body(home=home, chrome=chrome),
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
        "cdp_url": f"http://127.0.0.1:{_CDP_PORT}",
        "vnc_local": f"127.0.0.1:{_VNC_PORT}",
        "profile_dir": str(_PROFILE_DIR),
        "chrome_bin": str(chrome),
        "units_written": written,
        "units_started": started,
        "sign_in": "Open VNC 5912 (SSH tunnel), complete ChatGPT login in the browser profile.",
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
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
