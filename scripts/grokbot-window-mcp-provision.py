#!/usr/bin/env python3
"""Seed box MCP launcher + headers and optionally rewrite Grok systemd units.

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


def _live_unit_text(name: str) -> str | None:
    path = _SYSTEMD_USER / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _assert_gc1_units_write_safe(units: dict[str, str]) -> None:
    for name, body in units.items():
        live = _live_unit_text(name)
        if live is None:
            raise SystemExit(
                f"gc1 units --write refused: no live unit {name} to compare"
            )
        if live != body:
            raise SystemExit(
                f"gc1 units --write refused: generated {name} differs from live unit (would overwrite)"
            )


def _cmd_mcp(args: argparse.Namespace) -> dict[str, object]:
    profile = load_window_profile(args.slot)
    if args.skip_header:
        return provision_box_mcp_artifacts(profile, skip_header=True)
    return provision_box_mcp_artifacts(
        profile,
        force_header=bool(args.force),
    )


def _cmd_units(args: argparse.Namespace) -> dict[str, object]:
    profile = load_window_profile(args.slot)
    if args.write and profile.slot == "gc1" and not args.i_know_gc1:
        raise SystemExit(
            "gc1 units --write requires --i-know-gc1 (live units are host-managed)"
        )
    browser = provision_browser_for_profile(profile)
    units = systemd_grok_bundle(args.slot)
    if args.write and profile.slot == "gc1":
        _assert_gc1_units_write_safe(units)
    written: list[str] = []
    if args.write:
        _SYSTEMD_USER.mkdir(parents=True, exist_ok=True)
        for name, body in units.items():
            path = _SYSTEMD_USER / name
            if path.is_file() and path.read_text(encoding="utf-8") != body:
                bak = path.with_suffix(path.suffix + ".bak")
                bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
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
    mcp.add_argument(
        "--skip-header", action="store_true", help="Launcher only (token missing)"
    )
    mcp.add_argument(
        "--force",
        action="store_true",
        help="Overwrite host ~/.gateway/vortex-mcp-headers when content differs",
    )

    units = sub.add_parser(
        "units", help="Emit or write aligned systemd units (gc2 default)"
    )
    units.add_argument("--slot", required=True, choices=["gc1", "gc2", "9", "10"])
    units.add_argument(
        "--write", action="store_true", help="Write units to ~/.config/systemd/user"
    )
    units.add_argument(
        "--i-know-gc1",
        action="store_true",
        help="Allow gc1 --write only when generated units match live files exactly",
    )
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
