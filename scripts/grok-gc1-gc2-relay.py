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
from io_agent_gc2_browser import (
    format_systemd_environment_block,
    gc2_service_environment,
    provision_gc2_browser_config,
)

_SYSTEMD_USER_DIR = Path.home() / ".config/systemd/user"
_GC2_DATA_DIR = Path.home() / ".gateway/grok-io-gc2"

_XVFB_10_UNIT = """[Unit]
Description=Agent Xvfb :10 (Grok gc2) 1920x1200
After=network.target

[Service]
EnvironmentFile=/home/io/.config/io-agent-display.env
ExecStart=/usr/bin/Xvfb :10 -screen 0 ${IO_AGENT_GEOMETRY} -ac -nolisten tcp
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""

_VNC_5910_UNIT = """[Unit]
Description=x11vnc for agent display :10 (port 5910)
After=io-agent-xvfb-10.service
Requires=io-agent-xvfb-10.service

[Service]
ExecStart=/usr/bin/x11vnc -display :10 -localhost -no6 -rfbport 5910 -nopw -shared -forever -noxdamage
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""

def _grok_gc2_unit_body(*, home: Path) -> str:
    env_block = format_systemd_environment_block(gc2_service_environment(home=home))
    return f"""[Unit]
Description=Grok Bot gc2 agent profile on display :10 (CDP 9340)
After=io-agent-xvfb-10.service
Requires=io-agent-xvfb-10.service

[Service]
{env_block}
ExecStart="/opt/Grok Bot/grok-bot" --user-data-dir={home}/.gateway/grok-io-gc2 --remote-debugging-port=9340 --window-size=1920,1200 --window-position=0,0
ExecStartPost={home}/.venvs/universal/bin/python {home}/.config/io-agent-maximize.py :10
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def _provision_gc2(*, start: bool) -> dict[str, object]:
    _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    _GC2_DATA_DIR.mkdir(parents=True, exist_ok=True)
    browser = provision_gc2_browser_config()
    units = {
        "io-agent-xvfb-10.service": _XVFB_10_UNIT,
        "io-agent-vnc-5910.service": _VNC_5910_UNIT,
        "io-agent-grok-gc2.service": _grok_gc2_unit_body(home=Path.home()),
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
        "data_dir": str(_GC2_DATA_DIR),
        "browser_config": browser,
        "units_written": written,
        "units_started": started,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    prov = sub.add_parser("provision-gc2", help="Write and optionally start gc2 (:10 / :9340) systemd user units")
    prov.add_argument("--start", action="store_true", help="Enable and start the systemd user units immediately")

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
        out = _provision_gc2(start=bool(args.start))
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
