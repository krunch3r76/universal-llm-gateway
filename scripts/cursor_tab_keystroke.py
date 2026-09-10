#!/usr/bin/env python3
"""Wayland keystroke helper for the Cursor keystroke bridge (jupiter / Wayland).

evdev uinput + wl-copy, same substrate as ``orchestrator_tab_keystroke.py`` and
``grokbot_tab_keystroke.py``. Two ops the io-side watcher drives over SSH:

  open   raise Cursor → palette "New Chat" → paste opening message → Enter
  paste  raise Cursor → [focus tab by title] → focus chat input → paste → Enter

``paste`` is the per-turn wake: the in-tab agent has ended its turn, so a fresh
user message is the only thing that makes it read the bus again.

Invoke on the graphical host (SSH from io):
  WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 \\
    python3 scripts/cursor_tab_keystroke.py open --message-file F
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

_DEFAULT_REPO = os.environ.get(
    "CURSOR_BRIDGE_REPO", "/mnt/torus/projects/universal-llm-gateway"
)
_MODEL_QUERY = os.environ.get("CURSOR_BRIDGE_MODEL_QUERY", "composer")
_NEW_CHAT = os.environ.get("CURSOR_BRIDGE_NEW_CHAT", "ctrl_n")
_NEW_CHAT_CHORDS = ("ctrl_n", "ctrl_t", "palette")
_FOCUS_OPENERS = ("none", "ctrl_k", "ctrl_slash", "ctrl_shift_p")
_INPUT_FOCUS = ("ctrl_l", "none")
_FOCUS_VERIFY_CMD = os.environ.get("CURSOR_BRIDGE_FOCUS_VERIFY_CMD", "").strip()


def _active_window_title() -> tuple[str | None, str]:
    """Best-effort focused window title on the graphical host.

    Returns (title_or_none, probe_name). probe_name is ``none`` when no backend ran.
    """
    if _FOCUS_VERIFY_CMD:
        try:
            proc = subprocess.run(
                _FOCUS_VERIFY_CMD,
                shell=True,
                capture_output=True,
                text=True,
                timeout=8,
                env=os.environ,
            )
            if proc.returncode == 0:
                title = (proc.stdout or "").strip()
                if title:
                    return title, "env_cmd"
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None, "env_cmd_failed"
    try:
        proc = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
            env=os.environ,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            title = str(data.get("title") or "").strip()
            if title:
                return title, "hyprctl"
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None, "none"


def _verify_focus_title(expected: str) -> dict[str, object]:
    """Post-keystroke probe: focused window title must contain ``expected``.

    When no probe backend exists (COSMIC default), returns ``focus_verified=None``
    so the watcher can still enforce the TAB_READY gate without blocking all wakes.
    """
    observed, probe = _active_window_title()
    if probe == "none":
        return {"focus_verified": None, "focus_probe": probe}
    if probe.endswith("_failed") or not observed:
        return {
            "focus_verified": False,
            "focus_probe": probe,
            "reason": "focus_unverified",
        }
    if expected not in observed:
        return {
            "focus_verified": False,
            "focus_probe": probe,
            "reason": "focus_mismatch",
            "observed_title": observed,
            "expected_title": expected,
        }
    return {
        "focus_verified": True,
        "focus_probe": probe,
        "observed_title": observed,
    }


def _require_display() -> None:
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit("WAYLAND_DISPLAY unset — run on graphical session (jupiter)")


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


def _raise_cursor(repo: str) -> None:
    subprocess.run(
        ["cursor", "-r", repo],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


def _ui() -> UInput:
    cap = {
        e.EV_KEY: [
            e.KEY_LEFTCTRL,
            e.KEY_LEFTSHIFT,
            e.KEY_ENTER,
            e.KEY_ESC,
            e.KEY_V,
            e.KEY_P,
            e.KEY_L,
            e.KEY_T,
            e.KEY_K,
            e.KEY_SLASH,
        ]
    }
    return UInput(events=cap, name="cursor-bridge-kbd", bustype=e.BUS_USB)


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


def _paste_text_enter(ui: UInput, text: str, *, pause_s: float = 0.25) -> None:
    _wl_copy(text)
    time.sleep(0.08)
    _paste(ui)
    time.sleep(pause_s)
    _tap(ui, e.KEY_ENTER)


def _palette_run(ui: UInput, query: str, *, opener: str) -> None:
    """Open a palette (command or Glass quick-command), filter by query, Enter."""
    if opener == "ctrl_shift_p":
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_P)
    elif opener == "ctrl_slash":
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_SLASH)
    else:
        raise ValueError(f"unknown opener: {opener}")
    time.sleep(0.35)
    _paste_text_enter(ui, query, pause_s=0.3)
    time.sleep(0.5)


def _focus_run(ui: UInput, query: str, *, opener: str) -> None:
    """Focus an existing chat tab — operator default: Ctrl+K filter by title."""
    if opener == "ctrl_k":
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_K)
        time.sleep(0.35)
        _paste_text_enter(ui, query, pause_s=0.3)
        time.sleep(0.5)
    else:
        _palette_run(ui, query, opener=opener)


def _new_chat(ui: UInput, chord: str) -> None:
    if chord == "ctrl_n":
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_N)
    elif chord == "ctrl_t":
        _chord(ui, e.KEY_LEFTCTRL, e.KEY_T)
    elif chord == "palette":
        _palette_run(ui, "New Chat", opener="ctrl_shift_p")
    else:
        raise ValueError(f"unknown new-chat chord: {chord}")


def open_tab(
    message: str, *, repo: str, model_query: str, new_chat: str, dry_run: bool
) -> dict[str, object]:
    """Operator recipe (2026-09-10): Ctrl+N new chat → Ctrl+/ quick command → model → paste."""
    _require_display()
    plan = ["raise", f"new_chat:{new_chat}"]
    if model_query:
        plan.append(f"ctrl+/:{model_query}")
    plan += ["paste:message", "enter"]
    if dry_run:
        return {
            "dry_run": True,
            "op": "open",
            "steps": plan,
            "message_len": len(message),
        }
    _raise_cursor(repo)
    time.sleep(0.9)
    ui = _ui()
    try:
        _new_chat(ui, new_chat)
        time.sleep(0.6)
        if model_query:
            _palette_run(ui, model_query, opener="ctrl_slash")
        # New-chat input takes focus; longer settle so the paste lands in the box.
        time.sleep(0.4)
        _paste_text_enter(ui, message)
    finally:
        ui.close()
    return {"ok": True, "op": "open", "steps": plan, "message_len": len(message)}


def paste_message(
    message: str,
    *,
    repo: str,
    focus_title: str,
    focus_opener: str,
    input_focus: str,
    dry_run: bool,
) -> dict[str, object]:
    _require_display()
    plan = ["raise"]
    if focus_title and focus_opener != "none":
        plan.append(f"{focus_opener}:{focus_title}")
    if input_focus == "ctrl_l":
        plan.append("ctrl+l")
    plan += ["paste:message", "enter"]
    if dry_run:
        return {
            "dry_run": True,
            "op": "paste",
            "steps": plan,
            "message_len": len(message),
        }
    _raise_cursor(repo)
    time.sleep(0.9)
    ui = _ui()
    try:
        if focus_title and focus_opener != "none":
            _focus_run(ui, focus_title, opener=focus_opener)
        if input_focus == "ctrl_l":
            # Ctrl+L focuses the chat input regardless of where the caret sits;
            # without it a paste can land in an editor buffer.
            _chord(ui, e.KEY_LEFTCTRL, e.KEY_L)
            time.sleep(0.4)
        focus_check: dict[str, object] = {}
        if focus_title and focus_opener != "none":
            focus_check = _verify_focus_title(focus_title)
            if focus_check.get("focus_verified") is False:
                return {
                    "ok": False,
                    "op": "paste",
                    "steps": plan,
                    "message_len": 0,
                    **focus_check,
                }
        _paste_text_enter(ui, message)
    finally:
        ui.close()
    out: dict[str, object] = {
        "ok": True,
        "op": "paste",
        "steps": plan,
        "message_len": len(message),
    }
    if focus_title and focus_opener != "none":
        out.update(focus_check)
        if focus_check.get("focus_verified") is None:
            out["focus_verified"] = None
            out["focus_probe"] = focus_check.get("focus_probe", "none")
    return out


def _read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8").strip()
    if args.message:
        return args.message.strip()
    raise SystemExit("requires --message or --message-file")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    op = sub.add_parser(
        "open",
        help="Raise Cursor, Ctrl+N new chat, Ctrl+/ model, paste opening message",
    )
    op.add_argument("--message")
    op.add_argument("--message-file")
    op.add_argument("--repo", default=_DEFAULT_REPO)
    op.add_argument("--new-chat", choices=_NEW_CHAT_CHORDS, default=_NEW_CHAT)
    op.add_argument(
        "--model-query",
        default=_MODEL_QUERY,
        help="Ctrl+/ quick-command filter; empty skips",
    )
    op.add_argument("--dry-run", action="store_true")

    pm = sub.add_parser(
        "paste", help="Raise Cursor, focus chat input, paste wake message"
    )
    pm.add_argument("--message")
    pm.add_argument("--message-file")
    pm.add_argument("--repo", default=_DEFAULT_REPO)
    pm.add_argument(
        "--focus-title", default="", help="Chat title to select via palette (probe)"
    )
    pm.add_argument(
        "--focus-opener",
        choices=_FOCUS_OPENERS,
        default=os.environ.get("CURSOR_BRIDGE_FOCUS_OPENER", "ctrl_k"),
    )
    pm.add_argument(
        "--input-focus",
        choices=_INPUT_FOCUS,
        default=os.environ.get("CURSOR_BRIDGE_INPUT_FOCUS", "ctrl_l"),
    )
    pm.add_argument("--dry-run", action="store_true")

    sm = sub.add_parser("smoke", help="Raise Cursor only (no keys)")
    sm.add_argument("--repo", default=_DEFAULT_REPO)

    args = p.parse_args()
    if args.cmd == "smoke":
        _require_display()
        _raise_cursor(args.repo)
        out: dict[str, object] = {"ok": True, "op": "smoke"}
    elif args.cmd == "open":
        out = open_tab(
            _read_message(args),
            repo=args.repo,
            model_query=args.model_query,
            new_chat=args.new_chat,
            dry_run=args.dry_run,
        )
    else:
        out = paste_message(
            _read_message(args),
            repo=args.repo,
            focus_title=args.focus_title,
            focus_opener=args.focus_opener,
            input_focus=args.input_focus,
            dry_run=args.dry_run,
        )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
