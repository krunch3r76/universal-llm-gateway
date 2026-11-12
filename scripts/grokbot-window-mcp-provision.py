#!/usr/bin/env python3
"""Seed sand-data MCP launcher + headers and optionally rewrite Grok systemd units.

Usage:
  grokbot-window-mcp-provision.py mcp --slot gc1
  grokbot-window-mcp-provision.py mcp --slot gc2
  grokbot-window-mcp-provision.py units --slot gc2 [--write] [--start]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from grokbot_box_mcp.provision import provision_box_mcp_artifacts, provision_window_mcp
from grokbot_box_mcp.window_units import load_window_profile
from io_agent_grok_window import provision_browser_for_profile, systemd_grok_bundle

_SYSTEMD_USER = Path.home() / ".config/systemd/user"


def _cmd_mcp(args: argparse.Namespace) -> dict[str, object]:
    profile = load_window_profile(args.slot)
    if args.skip_header:
        return provision_box_mcp_artifacts(profile, skip_header=True)
    return provision_window_mcp(args.slot)


def _cmd_units(args: argparse.Namespace) -> dict[str, object]:
    profile = load_window_profile(args.slot)
    browser = provision_browser_for_profile(profile)
    units = systemd_grok_bundle(args.slot)
    written: list[str] = []
    if args.write:
        _SYSTEMD_USER.mkdir(parents=True, exist_ok=True)
        for name, body in units.items():
            path = _SYSTEMD_USER / name
            path.write_text(body, encoding="utf-8")
            written.append(str(path))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    started: list[str] = []
    if args.start:
        if not written:
            raise SystemExit("--start requires --write")
        for name in units:
            subprocess.run(["systemctl", "--user", "enable", "--now", name], check=True)
            started.append(name)
    return {
        "ok": True,
        "slot": profile.slot,
        "browser_config": browser,
        "units": list(units.keys()),
        "units_written": written,
        "units_started": started,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    mcp = sub.add_parser("mcp", help="Install box launcher + header seed for a window")
    mcp.add_argument("--slot", required=True, choices=["gc1", "gc2", "9", "10"])
    mcp.add_argument("--skip-header", action="store_true", help="Launcher only (token missing)")

    units = sub.add_parser("units", help="Emit or write aligned systemd units")
    units.add_argument("--slot", required=True, choices=["gc1", "gc2", "9", "10"])
    units.add_argument("--write", action="store_true", help="Write units to ~/.config/systemd/user")
    units.add_argument("--start", action="store_true", help="Enable --now after write")

    args = parser.parse_args()
    if args.cmd == "mcp":
        out = _cmd_mcp(args)
    else:
        out = _cmd_units(args)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
