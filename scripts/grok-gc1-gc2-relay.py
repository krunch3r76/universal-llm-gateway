#!/usr/bin/env python3
"""CLI for gc2 (:10 / :9340) provisioning and repeatable 1-to-1 Electron paste/read relay (gc1 <-> gc2).

Subcommands:
  provision-gc2  Install and start user systemd units for Xvfb :10, x11vnc 5910, and Grok Bot gc2 (CDP 9340).
  read           Select an agent row in an Electron window (:8/:9/:10) and read its latest settled prose.
  paste          Select an agent row in an Electron window (:8/:9/:10) and paste + submit a message.
  relay          Run a repeatable 1-to-1 copy-and-paste relay between two Electron endpoints (e.g. gc1 <-> gc2)
                 until max exchanges or a bus handoff marker (HANDOFF_TO_BUS / BRIDGE_WAKE).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from cursor_bridge.electron_chat_relay import (
    ElectronEndpoint,
    paste_endpoint_message,
    read_endpoint_turn,
    run_one_to_one_relay,
)
from cursor_bridge.grok_window_bindings import promote, status, write_routing
from grokbot_box_mcp.provision import provision_box_mcp_artifacts
from grokbot_box_mcp.window_units import load_window_profile
from io_agent_grok_window import provision_browser_for_profile, systemd_grok_bundle

_SYSTEMD_USER_DIR = Path.home() / ".config/systemd/user"


def _provision_gc2(*, start: bool, seed_mcp: bool = True) -> dict[str, object]:
    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    profile = load_window_profile("gc2")
    profile.user_data_dir.mkdir(parents=True, exist_ok=True)
    browser = provision_browser_for_profile(profile)
    units = systemd_grok_bundle("gc2")
    mcp: dict[str, object] | None = None
    if seed_mcp:
        try:
            mcp = provision_box_mcp_artifacts(profile)
        except RuntimeError as exc:
            mcp = {"ok": False, "error": str(exc)}
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
        "data_dir": str(profile.user_data_dir),
        "browser_config": browser,
        "box_mcp": mcp,
        "units_written": written,
        "units_started": started,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    prov = sub.add_parser("provision-gc2", help="Write and optionally start gc2 (:10 / :9340) systemd user units")
    prov.add_argument("--start", action="store_true", help="Enable and start the systemd user units immediately")
    prov.add_argument(
        "--no-seed-mcp",
        action="store_true",
        help="Skip sand-data MCP launcher/header seed (token or launcher-only failures)",
    )

    rd = sub.add_parser("read", help="Select sidebar agent and read latest settled prose")
    rd.add_argument("--label", default="gc1")
    rd.add_argument("--app", default="grok")
    rd.add_argument("--display", default="9")
    rd.add_argument("--title", default="")
    rd.add_argument("--agent-id", default="")
    rd.add_argument("--no-select", action="store_true")

    ps = sub.add_parser("paste", help="Select sidebar agent and paste + submit a message")
    ps.add_argument("--label", default="gc1")
    ps.add_argument("--app", default="grok")
    ps.add_argument("--display", default="9")
    ps.add_argument("--title", default="")
    ps.add_argument("--agent-id", default="")
    ps.add_argument("--message", required=True)
    ps.add_argument("--no-select", action="store_true")

    rl = sub.add_parser("relay", help="Run repeatable 1-to-1 Electron paste/read relay (gc1 <-> gc2)")
    rl.add_argument("--left-label", default="gc1")
    rl.add_argument("--left-app", default="grok")
    rl.add_argument("--left-display", default="9")
    rl.add_argument("--left-title", default="Stage Manager (gc1)")
    rl.add_argument("--left-agent-id", default="")
    rl.add_argument("--right-label", default="gc2")
    rl.add_argument("--right-app", default="grok")
    rl.add_argument("--right-display", default="10")
    rl.add_argument("--right-title", default="Stage Manager (gc2)")
    rl.add_argument("--right-agent-id", default="")
    rl.add_argument("--seed-message", default="")
    rl.add_argument("--max-exchanges", type=int, default=10)
    rl.add_argument("--poll-interval-s", type=float, default=2.0)
    rl.add_argument("--turn-timeout-s", type=float, default=180.0)

    sub.add_parser("status", help="Print window config plus live routing state")
    sub.add_parser("show-routing", help="Print window config plus live routing state")

    route = sub.add_parser(
        "set-routing",
        help="Set default grok-bot window and insyncerator window",
    )
    route.add_argument("--default", required=True, help=":9 or :10 for grok-bot / impresario")
    route.add_argument("--insyncerator", required=True, help=":9 or :10 for insyncerator")
    route.add_argument("--updated-by", default="", help="Seat or agent writing the state")

    promo = sub.add_parser(
        "promote",
        help="Make a display the grok-bot home and park insyncerator on the other",
    )
    promo.add_argument("display", help=":9 or :10")
    promo.add_argument("--updated-by", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.cmd == "provision-gc2":
        out = _provision_gc2(start=bool(args.start), seed_mcp=not args.no_seed_mcp)
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "read":
        ep = ElectronEndpoint(
            label=args.label,
            app=args.app,
            display=args.display,
            title=args.title,
            agent_id=args.agent_id,
        )
        out = asyncio.run(read_endpoint_turn(ep, select=not args.no_select))
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "paste":
        ep = ElectronEndpoint(
            label=args.label,
            app=args.app,
            display=args.display,
            title=args.title,
            agent_id=args.agent_id,
        )
        out = asyncio.run(
            paste_endpoint_message(ep, args.message, select=not args.no_select)
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "relay":
        left = ElectronEndpoint(
            label=args.left_label,
            app=args.left_app,
            display=args.left_display,
            title=args.left_title,
            agent_id=args.left_agent_id,
        )
        right = ElectronEndpoint(
            label=args.right_label,
            app=args.right_app,
            display=args.right_display,
            title=args.right_title,
            agent_id=args.right_agent_id,
        )
        out = asyncio.run(
            run_one_to_one_relay(
                left,
                right,
                seed_message=args.seed_message or None,
                max_exchanges=args.max_exchanges,
                poll_interval_s=args.poll_interval_s,
                turn_timeout_s=args.turn_timeout_s,
            )
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd in {"show-routing", "status"}:
        print(json.dumps(status(), indent=2))
        return 0
    if args.cmd == "set-routing":
        state = write_routing(
            default_window=args.default,
            insyncerator_window=args.insyncerator,
            source="set-routing",
            updated_by=args.updated_by or None,
        )
        print(json.dumps(state, indent=2))
        return 0
    if args.cmd == "promote":
        state = promote(args.display, updated_by=args.updated_by or None)
        print(json.dumps(state, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
