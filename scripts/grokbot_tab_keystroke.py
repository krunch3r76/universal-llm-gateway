#!/usr/bin/env python3
"""Wayland keystroke helper for Grok Bot treasury routine (orion-node / COSMIC).

Mirrors orchestrator_tab_keystroke.py: evdev uinput + wl-copy paste.
Raises Grok Bot via grokbot:// deep link, then per hop:

  Ctrl+N → paste bot picker query → Enter → paste routine message → Enter

Fresh chat to Treasury Scout each cycle (no in-thread context tax).

Invoke on the graphical host (SSH from io):
  WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 \\
    python3 scripts/grokbot_tab_keystroke.py launch \\
    --message-file tmp/prompts/grok-treasury-routine-start.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from evdev import UInput, ecodes as e

_REPO = Path(__file__).resolve().parents[1]
_GROKBOT_URI = os.environ.get("GROKBOT_OPEN_URI", "grokbot://app/v1/open")
_BOT_QUERY = os.environ.get("GROKBOT_TARGET_QUERY", "treasury scout")


def _require_display() -> None:
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("WAYLAND_DISPLAY unset — run on graphical session (orion-node)")


def _wl_copy(text: str) -> None:
    proc = subprocess.run(
        ["wl-copy", "--paste-once", "--trim-newline", text],
        env=os.environ,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if proc.returncode != 0:
        raise SystemExit(f"wl-copy failed rc={proc.returncode}")


def _raise_grokbot() -> None:
    # xdg-open blocks while the handler runs — fire-and-forget; Grok is usually already up.
    subprocess.Popen(
        ["xdg-open", _GROKBOT_URI],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _ui() -> UInput:
    cap = {
        e.EV_KEY: [
            e.KEY_LEFTCTRL,
            e.KEY_LEFTSHIFT,
            e.KEY_ENTER,
            e.KEY_V,
            e.KEY_N,
        ]
    }
    return UInput(events=cap, name="grokbot-routine-kbd", bustype=e.BUS_USB)


def _syn(ui: UInput) -> None:
    ui.write(e.EV_SYN, e.SYN_REPORT, 0)
    ui.syn()


def _key_down(ui: UInput, key: int) -> None:
    ui.write(e.EV_KEY, key, 1)
    _syn(ui)


def _key_up(ui: UInput, key: int) -> None:
    ui.write(e.EV_KEY, key, 0)
    _syn(ui)


def _tap(ui: UInput, key: int, delay: float = 0.03) -> None:
    _key_down(ui, key)
    time.sleep(delay)
    _key_up(ui, key)
    time.sleep(delay)


def _chord(ui: UInput, *keys: int) -> None:
    for key in keys:
        _key_down(ui, key)
        time.sleep(0.02)
    time.sleep(0.04)
    for key in reversed(keys):
        _key_up(ui, key)
        time.sleep(0.02)


def _paste(ui: UInput) -> None:
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_V)


def _paste_and_enter(ui: UInput, text: str, *, pause_s: float = 0.15) -> None:
    _wl_copy(text)
    time.sleep(0.08)
    _paste(ui)
    time.sleep(pause_s)
    _tap(ui, e.KEY_ENTER)


def _new_chat_to_bot(ui: UInput, bot_query: str) -> None:
    """Ctrl+N new chat → picker query → Enter (To: Treasury Scout)."""
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_N)
    time.sleep(0.45)
    _paste_and_enter(ui, bot_query, pause_s=0.25)
    time.sleep(0.55)


def launch_routine_message(
    message: str,
    *,
    bot_query: str = _BOT_QUERY,
    dry_run: bool = False,
    focus_delay_s: float = 1.2,
) -> dict[str, object]:
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "grokbot_uri": _GROKBOT_URI,
            "bot_query": bot_query,
            "steps": ["ctrl+n", f"paste:{bot_query}", "enter", "paste:routine", "enter"],
            "message_preview": message[:160],
            "message_len": len(message),
        }
    _raise_grokbot()
    time.sleep(focus_delay_s)
    ui = _ui()
    try:
        _new_chat_to_bot(ui, bot_query)
        _paste_and_enter(ui, message)
    finally:
        ui.close()
    return {
        "ok": True,
        "message_len": len(message),
        "grokbot_uri": _GROKBOT_URI,
        "bot_query": bot_query,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("launch", help="Raise Grok Bot, paste routine message, Enter")
    lp.add_argument("--message", help="Routine text (e.g. ROUTINE START …)")
    lp.add_argument("--message-file", help="Read routine text from file")
    lp.add_argument("--focus-delay-s", type=float, default=1.2)
    lp.add_argument(
        "--bot-query",
        default=os.environ.get("GROKBOT_TARGET_QUERY", "treasury scout"),
        help="Grok Bot new-chat picker filter (default: treasury scout)",
    )
    lp.add_argument("--dry-run", action="store_true")
    sub.add_parser("smoke", help="xdg-open Grok Bot only")

    args = p.parse_args()
    if args.cmd == "smoke":
        _require_display()
        _raise_grokbot()
        print(json.dumps({"ok": True, "action": "raise_grokbot"}))
        return 0
    if args.cmd == "launch":
        if args.message_file:
            message = Path(args.message_file).read_text(encoding="utf-8").strip()
        elif args.message:
            message = args.message.strip()
        else:
            raise SystemExit("launch requires --message or --message-file")
        out = launch_routine_message(
            message,
            bot_query=args.bot_query,
            dry_run=args.dry_run,
            focus_delay_s=args.focus_delay_s,
        )
        print(json.dumps(out, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
