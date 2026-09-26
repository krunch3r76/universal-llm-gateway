#!/usr/bin/env python3
"""Same-tab Agents followup: focus window, Ctrl+K chat title, paste, Ctrl+Enter.

Preserves the composer model. Does not send Ctrl+T. Used when a hop would land on
the default model. Landing proof is the hub watcher, not this script.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from evdev import UInput
from evdev import ecodes as e

_DEFAULT_REPO = "/mnt/torus/projects/universal-llm-gateway"


def _require_display() -> None:
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("WAYLAND_DISPLAY unset — run on the graphical host")


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


def _focus_window(title_substr: str, app_id: str) -> dict:
    helper = Path(__file__).with_name("cosmic_focus_window.py")
    proc = subprocess.run(
        [
            sys.executable,
            str(helper),
            "activate",
            "--app-id",
            app_id,
            "--title-substr",
            title_substr,
        ],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        verdict = {"ok": False, "raw": (proc.stdout + proc.stderr)[-400:]}
    if not verdict.get("ok"):
        raise SystemExit(json.dumps({"ok": False, "phase": "focus", **verdict}))
    time.sleep(0.6)
    return verdict


def _ui() -> UInput:
    keys = list(range(e.KEY_ESC, e.KEY_S + 1))
    for extra in (
        e.KEY_D,
        e.KEY_F,
        e.KEY_G,
        e.KEY_H,
        e.KEY_J,
        e.KEY_K,
        e.KEY_L,
        e.KEY_V,
        e.KEY_N,
        e.KEY_P,
        e.KEY_SLASH,
        e.KEY_LEFTSHIFT,
        e.KEY_RIGHTSHIFT,
        e.KEY_RIGHTCTRL,
        e.KEY_LEFTALT,
        e.KEY_LEFTMETA,
    ):
        if extra not in keys:
            keys.append(extra)
    ui = UInput(
        events={e.EV_KEY: keys}, name="stall-tab-followup-kbd", bustype=e.BUS_USB
    )
    time.sleep(0.5)
    return ui


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


def followup_named_chat(
    message: str,
    *,
    chat_title: str,
    focus_title: str,
    focus_app_id: str,
    dry_run: bool,
) -> dict[str, object]:
    """Focus one IDE chat by title, paste ``message``, and submit with Ctrl+Enter.

    Ctrl+K selects ``chat_title``. This does not send Ctrl+T, so the composer
    on that tab stays. ``dry_run`` returns the step plan and sends nothing.
    """
    plan = [
        f"focus:{focus_title}",
        f"ctrl_k:{chat_title}",
        "ctrl_l",
        "paste",
        "ctrl_enter",
    ]
    if dry_run:
        return {
            "dry_run": True,
            "op": "named_followup",
            "steps": plan,
            "chat_title": chat_title,
            "message_len": len(message),
        }
    _require_display()
    focused = _focus_window(focus_title, focus_app_id)
    ui = _ui()
    try:
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_K)
        time.sleep(0.35)
        _wl_copy(chat_title)
        time.sleep(0.05)
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_V)
        time.sleep(0.25)
        _tap(ui, e.KEY_ENTER)
        time.sleep(0.5)
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_L)
        time.sleep(0.4)
        _wl_copy(message)
        time.sleep(0.08)
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_V)
        time.sleep(0.25)
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_ENTER)
    finally:
        ui.close()
    return {
        "ok": True,
        "op": "named_followup",
        "steps": plan,
        "chat_title": chat_title,
        "focused": focused.get("activated"),
        "message_len": len(message),
    }


def main() -> int:
    """CLI for a same-tab IDE followup. Reads the message file and prints JSON.

    Requires the GUI-host display. Returns 0 on a dry-run or a completed paste.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--message-file", required=True)
    p.add_argument("--chat-title", required=True)
    p.add_argument("--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO))
    p.add_argument("--focus-title", default="Cursor Agents")
    p.add_argument("--focus-app-id", default="cursor")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    message = Path(args.message_file).read_text(encoding="utf-8").strip()
    out = followup_named_chat(
        message,
        chat_title=args.chat_title,
        focus_title=args.focus_title,
        focus_app_id=args.focus_app_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("dry_run") or out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
