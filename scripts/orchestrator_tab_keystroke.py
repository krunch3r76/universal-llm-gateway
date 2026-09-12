#!/usr/bin/env python3
"""Wayland keystroke helper for Cursor orchestrator tab handoff (orion-node / COSMIC).

Uses evdev uinput (writable /dev/uinput on orion-node) + wl-copy for paste.
Requires: WAYLAND_DISPLAY, XDG_RUNTIME_DIR, focused-or-raised Cursor window.

Not imported — invoked via SSH on the graphical host:
  WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 \\
    python3 scripts/orchestrator_tab_keystroke.py launch --message 'resume 10223'
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

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_REPO = "/mnt/torus/projects/universal-llm-gateway"


def _require_display() -> None:
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise SystemExit(
            "WAYLAND_DISPLAY unset — run on graphical session (orion-node)"
        )


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


def _raise_cursor_uri(folder_uri: str) -> None:
    """Focus the window that already has ``folder_uri`` open.

    Opening a folder URI that is open in some window focuses that window instead
    of creating one — the only compositor-independent raise available for a
    Remote-SSH window (``cursor -r <path>`` would open the path as a *local*
    workspace, and COSMIC exposes no focus API). URI shape:
    ``vscode-remote://ssh-remote%2B<authority>/<repo>`` from the GUI host's
    ``~/.config/Cursor/User/workspaceStorage/*/workspace.json``.
    """
    subprocess.run(
        ["cursor", "--folder-uri", folder_uri],
        env=os.environ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )


def _focus_window(title_substr: str, app_id: str, *, settle_s: float = 0.6) -> dict:
    """Focus the toplevel matching ``app_id`` + ``title_substr`` through the compositor.

    Native-Wayland Cursor cannot raise itself on ``cursor --folder-uri`` (no activation
    token; 2026-09-12 04:24Z the URI went to Firefox) and the COSMIC launcher driven by
    keys fuzzy-matched UMLet, then launched a second IDE window (06:09Z / 06:11Z) — so
    every hop typed into whatever window happened to be focused. cosmic-comp does offer
    ``zcosmic_toplevel_manager_v1.activate`` to ordinary clients; ``cosmic_focus_window.py``
    lists toplevels, activates exactly one match and reports ``focused`` from the
    handle's state. Anything but ``focused: true`` aborts before a single key is sent.
    """
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
        verdict = {"ok": False, "raw": proc.stdout[-400:] + proc.stderr[-400:]}
    if not verdict.get("ok"):
        raise SystemExit(json.dumps({"ok": False, "phase": "focus", **verdict}))
    time.sleep(settle_s)
    return verdict


def _ui() -> UInput:
    cap = {
        e.EV_KEY: [
            e.KEY_LEFTCTRL,
            e.KEY_LEFTSHIFT,
            e.KEY_LEFTALT,
            e.KEY_LEFTMETA,
            e.KEY_ENTER,
            e.KEY_V,
            e.KEY_P,
            e.KEY_N,
            e.KEY_L,
            e.KEY_SLASH,
        ]
    }
    return UInput(events=cap, name="orchestrator-handoff-kbd", bustype=e.BUS_USB)


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


def _command_palette(ui: UInput) -> None:
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_P)
    time.sleep(0.35)


def _ctrl_slash_palette(ui: UInput) -> None:
    """Glass quick-command chord (operator recipe: ctrl-/ then filter)."""
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_SLASH)
    time.sleep(0.35)


def _quick_command(ui: UInput, query: str, *, opener: str = "ctrl_slash") -> None:
    if opener == "ctrl_slash":
        _ctrl_slash_palette(ui)
    elif opener == "ctrl_shift_p":
        _command_palette(ui)
    else:
        raise ValueError(f"unknown opener: {opener}")
    _wl_copy(query)
    time.sleep(0.05)
    _paste(ui)
    time.sleep(0.25)
    _tap(ui, e.KEY_ENTER)
    time.sleep(0.45)


def _palette_run(ui: UInput, query: str) -> None:
    _wl_copy(query)
    time.sleep(0.05)
    _command_palette(ui)
    _paste(ui)
    time.sleep(0.25)
    _tap(ui, e.KEY_ENTER)
    time.sleep(0.45)


def launch_new_chat_with_message(
    message: str,
    *,
    repo: str,
    palette_query: str = "New Chat",
    dry_run: bool = False,
    raise_window: bool = True,
    raise_uri: str | None = None,
    focus_title: str | None = None,
    focus_app_id: str = "cursor",
) -> dict[str, object]:
    """Open a new chat in a Cursor window and send ``message``.

    Focus order: ``focus_title`` (+ ``focus_app_id``: compositor ``activate`` on the
    one matching toplevel, verified before any key is sent — the only raise that works
    for a native-Wayland Cursor) ≻ ``raise_uri`` (``cursor --folder-uri``, for
    compositors that honour it) ≻ ``raise_window`` (legacy ``cursor -r <repo>``, local
    workspaces only) ≻ neither (paste into whatever is in front — hop 3 on 2026-09-11
    landed in Firefox this way; only for an operator who is watching).
    """
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "palette_query": palette_query,
            "raise_window": raise_window,
            "raise_uri": raise_uri,
            "focus_title": focus_title,
            "message_preview": message[:120],
        }
    focused: dict = {}
    if focus_title:
        focused = _focus_window(focus_title, focus_app_id)
    elif raise_uri:
        _raise_cursor_uri(raise_uri)
        time.sleep(1.5)
    elif raise_window:
        _raise_cursor(repo)
        time.sleep(0.9)
    ui = _ui()
    try:
        _palette_run(ui, palette_query)
        _wl_copy(message)
        time.sleep(0.08)
        _paste(ui)
        time.sleep(0.15)
        _tap(ui, e.KEY_ENTER)
    finally:
        ui.close()
    return {
        "ok": True,
        "palette_query": palette_query,
        "focus_title": focus_title,
        "focused": focused.get("activated"),
        "message_len": len(message),
    }


def glass_quick_command(
    query: str,
    *,
    repo: str,
    opener: str = "ctrl_slash",
    dry_run: bool = False,
) -> dict[str, object]:
    """Raise Cursor, open Glass quick command (ctrl-/ or palette), run query."""
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "opener": opener,
            "query": query,
        }
    _raise_cursor(repo)
    time.sleep(0.9)
    ui = _ui()
    try:
        _quick_command(ui, query, opener=opener)
    finally:
        ui.close()
    return {"ok": True, "opener": opener, "query": query}


def select_composer_model(
    *,
    repo: str,
    model_query: str = "Composer 2.5",
    dry_run: bool = False,
) -> dict[str, object]:
    """Try ctrl-/composer then pick model — operator Glass default-model recipe."""
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "steps": ["ctrl-/", "composer", "enter", f"filter:{model_query}", "enter"],
        }
    _raise_cursor(repo)
    time.sleep(0.9)
    ui = _ui()
    try:
        _quick_command(ui, "composer", opener="ctrl_slash")
        _quick_command(ui, model_query, opener="ctrl_slash")
    finally:
        ui.close()
    return {"ok": True, "model_query": model_query}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser(
        "launch", help="Raise Cursor, new chat via palette, paste message"
    )
    lp.add_argument("--message", help="First user message (e.g. resume 10223 …)")
    lp.add_argument(
        "--message-file", help="Read message from file (preferred for multiline)"
    )
    lp.add_argument(
        "--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO)
    )
    lp.add_argument(
        "--palette-query",
        default=os.environ.get("ORCHESTRATOR_PALETTE_QUERY", "New Chat"),
        help="Command palette filter for new chat (default: New Chat)",
    )
    lp.add_argument("--dry-run", action="store_true")
    lp.add_argument(
        "--no-raise",
        action="store_true",
        help="Do not run `cursor -r <repo>` first (paste into whatever window is focused)",
    )
    lp.add_argument(
        "--raise-uri",
        default=None,
        help="Focus the window holding this folder URI first (vscode-remote://ssh-remote%%2B…/repo)",
    )
    lp.add_argument(
        "--focus-title",
        default=None,
        help="Focus the toplevel whose title contains this (compositor activate, "
        "verified) before typing — e.g. 'Cursor Agents'; overrides --raise-uri",
    )
    lp.add_argument(
        "--focus-app-id",
        default="cursor",
        help="app_id substring the focused toplevel must carry (default: cursor)",
    )
    gq = sub.add_parser(
        "glass-cmd", help="Raise Cursor, ctrl-/ (or palette), run query"
    )
    gq.add_argument("query", help="Filter text after opening quick command")
    gq.add_argument(
        "--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO)
    )
    gq.add_argument(
        "--opener",
        choices=("ctrl_slash", "ctrl_shift_p"),
        default=os.environ.get("ORCHESTRATOR_GLASS_OPENER", "ctrl_slash"),
    )
    gq.add_argument("--dry-run", action="store_true")
    mc = sub.add_parser(
        "select-composer",
        help="ctrl-/composer<CR> then ctrl-/Composer 2.5<CR> (Glass model pick probe)",
    )
    mc.add_argument(
        "--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO)
    )
    mc.add_argument(
        "--model-query",
        default=os.environ.get("ORCHESTRATOR_MODEL_QUERY", "Composer 2.5"),
    )
    mc.add_argument("--dry-run", action="store_true")
    sub.add_parser("smoke", help="Raise Cursor only (no keys)")

    args = p.parse_args()
    if args.cmd == "smoke":
        _require_display()
        if args.dry_run if hasattr(args, "dry_run") else False:
            print({"dry_run": True})
            return 0
        _raise_cursor(getattr(args, "repo", _DEFAULT_REPO))
        print('{"ok": true, "action": "raise"}')
        return 0
    if args.cmd == "launch":
        if args.message_file:
            message = Path(args.message_file).read_text(encoding="utf-8").strip()
        elif args.message:
            message = args.message.strip()
        else:
            raise SystemExit("launch requires --message or --message-file")
        out = launch_new_chat_with_message(
            message,
            repo=args.repo,
            palette_query=args.palette_query,
            dry_run=args.dry_run,
            raise_window=not args.no_raise,
            raise_uri=args.raise_uri,
            focus_title=args.focus_title,
            focus_app_id=args.focus_app_id,
        )
        import json

        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "glass-cmd":
        import json

        out = glass_quick_command(
            args.query,
            repo=args.repo,
            opener=args.opener,
            dry_run=args.dry_run,
        )
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "select-composer":
        import json

        out = select_composer_model(
            repo=args.repo,
            model_query=args.model_query,
            dry_run=args.dry_run,
        )
        print(json.dumps(out, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
