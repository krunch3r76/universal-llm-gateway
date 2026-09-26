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


def _wl_copy(text: str) -> subprocess.Popen[bytes]:
    """Hold the clipboard until the caller pastes.

    ``--paste-once`` exits on the first read. A manager or the shell consumes
    it during the new-tab wait, so Ctrl+V then inserts nothing. Do not call
    this between Ctrl+T and Ctrl+V — that gap is a sleep only.
    """
    proc = subprocess.Popen(
        ["wl-copy", "--foreground", "--trim-newline"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ,
    )
    if proc.stdin is None:
        raise SystemExit("wl-copy stdin missing")
    proc.stdin.write(text.encode())
    proc.stdin.close()
    time.sleep(0.3)
    check = subprocess.run(
        ["wl-paste", "--no-newline"],
        capture_output=True,
        env=os.environ,
        timeout=5,
    )
    got = check.stdout.decode(errors="replace")
    if check.returncode != 0 or not got:
        proc.kill()
        raise SystemExit(f"clipboard empty after wl-copy rc={check.returncode}")
    return proc


def _release_clipboard(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()


def _raise_cursor(repo: str) -> None:
    subprocess.run(
        ["cursor", "-r", repo],
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


def _is_glass_title(title: str) -> bool:
    """Glass and the IDE are separate toplevels. Their titles are not one string."""
    return "glass" in title.lower()


def _list_cursor_toplevels() -> list[dict]:
    helper = Path(__file__).with_name("cosmic_focus_window.py")
    proc = subprocess.run(
        [sys.executable, str(helper), "list"],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise SystemExit(
            json.dumps({"ok": False, "phase": "focus", "raw": proc.stdout[-400:]})
        ) from None
    rows = data.get("toplevels") or []
    return [r for r in rows if "cursor" in str(r.get("app_id") or "").lower()]


def _pick_cursor_window(role: str, title_substr: str | None, repo: str) -> dict:
    """Pick the IDE window or the Glass window. Never treat them as the same title.

    Either may be open. A loose ``cursor`` match is ambiguous. IDE defaults to
    the toplevel whose title contains the repo name. Glass is a title that
    contains ``Glass``. Zero or several matches abort before any key.
    """
    rows = _list_cursor_toplevels()
    if role == "glass":
        pool = [r for r in rows if _is_glass_title(str(r.get("title") or ""))]
    else:
        pool = [r for r in rows if not _is_glass_title(str(r.get("title") or ""))]
    if title_substr:
        needle = title_substr.lower()
        pool = [r for r in pool if needle in str(r.get("title") or "").lower()]
    elif role == "ide":
        repo_name = Path(repo).name.lower()
        named = [r for r in pool if repo_name in str(r.get("title") or "").lower()]
        if named:
            pool = named
    if len(pool) != 1:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "phase": "focus",
                    "role": role,
                    "reason": "window_not_unique",
                    "note": "Glass and the IDE have different titles.",
                    "titles": [r.get("title") for r in rows],
                }
            )
        )
    return pool[0]


def _ui() -> UInput:
    """Open a virtual keyboard that udev tags ID_INPUT_KEYBOARD.

    udev's input_id sets that property only when key bits 1–31 are all set
    (ESC, digits, Q–S — systemd ``FLAGS_SET(bitmask_key[0], 0xFFFFFFFE)``).
    The old 13-key set only earned ID_INPUT_KEY. libinput then will not
    attach the device as the seat keyboard — hops report write-ok and
    COSMIC activate-ok while Cursor Agents never sees a key (jupiter
    2026-09-13 probe: /dev/input/event23, KEY_A, 3s grace, empty composer).
    """
    keys = list(range(e.KEY_ESC, e.KEY_S + 1))
    for extra in (
        e.KEY_D,
        e.KEY_V,
        e.KEY_P,
        e.KEY_L,
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
        events={e.EV_KEY: keys}, name="orchestrator-handoff-kbd", bustype=e.BUS_USB
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


def _paste(ui: UInput) -> None:
    """Ctrl+V slow enough for a composer that just took focus."""
    _key_down(ui, e.KEY_LEFTCTRL)
    time.sleep(0.18)
    _key_down(ui, e.KEY_V)
    time.sleep(0.15)
    _key_up(ui, e.KEY_V)
    time.sleep(0.08)
    _key_up(ui, e.KEY_LEFTCTRL)


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
    clip = _wl_copy(query)
    try:
        time.sleep(0.05)
        _paste(ui)
    finally:
        _release_clipboard(clip)
    time.sleep(0.25)
    _tap(ui, e.KEY_ENTER)
    time.sleep(0.45)


def _release_modifiers(ui: UInput) -> None:
    """Drop Shift before Ctrl+T. A held Shift turns the IDE new-tab chord into Ctrl+Shift+T."""
    for key in (
        e.KEY_LEFTSHIFT,
        e.KEY_RIGHTSHIFT,
        e.KEY_LEFTCTRL,
        e.KEY_RIGHTCTRL,
        e.KEY_LEFTALT,
        e.KEY_LEFTMETA,
    ):
        _key_up(ui, key)


def _new_chat(ui: UInput) -> None:
    """IDE new tab: Ctrl+T with Shift released. Glass uses ctrl-/ and does not call this."""
    _release_modifiers(ui)
    time.sleep(0.05)
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_T)
    time.sleep(0.45)


def _submit_composer(ui: UInput) -> None:
    """Agents composer: Enter is newline. Ctrl+Enter sends."""
    _chord(ui, e.KEY_LEFTCTRL, e.KEY_ENTER)


def followup_existing_chat_with_message(
    message: str,
    *,
    repo: str,
    dry_run: bool = False,
    raise_window: bool = True,
    focus_title: str | None = None,
    focus_app_id: str = "cursor",
) -> dict[str, object]:
    """Focus the lock-holder IDE window, paste ``message``, Ctrl+Enter — no Ctrl+T."""
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "steps": ["paste", "ctrl_enter"],
            "raise_window": raise_window,
            "focus_title": focus_title,
            "message_preview": message[:120],
        }
    focused: dict = {}
    if focus_title or raise_window:
        chosen = _pick_cursor_window("ide", focus_title, repo)
        focused = _focus_window(str(chosen["title"]), focus_app_id)
    clip = _wl_copy(message)
    ui = _ui()
    try:
        time.sleep(0.08)
        _paste(ui)
        time.sleep(0.25)
        _submit_composer(ui)
    finally:
        ui.close()
        _release_clipboard(clip)
    return {
        "ok": True,
        "steps": ["paste", "ctrl_enter"],
        "focus_title": focus_title,
        "focused": focused.get("activated"),
        "message_len": len(message),
    }


def launch_new_chat_with_message(
    message: str,
    *,
    repo: str,
    dry_run: bool = False,
    raise_window: bool = True,
    focus_title: str | None = None,
    focus_app_id: str = "cursor",
) -> dict[str, object]:
    """Focus the IDE window, Ctrl+T (new tab), paste ``message``, Ctrl+Enter.

    Clipboard is armed before Ctrl+T. The only step between Ctrl+T and Ctrl+V
    is a wait — a ``wl-copy`` in that gap empties ``--paste-once`` before the
    new composer reads it. Glass is a different toplevel and uses ctrl-/.
    """
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "steps": ["clipboard", "ctrl_t", "wait", "paste", "ctrl_enter"],
            "raise_window": raise_window,
            "focus_title": focus_title,
            "message_preview": message[:120],
        }
    focused: dict = {}
    if focus_title or raise_window:
        chosen = _pick_cursor_window("ide", focus_title, repo)
        focused = _focus_window(str(chosen["title"]), focus_app_id)
    clip = _wl_copy(message)
    ui = _ui()
    try:
        _new_chat(ui)
        time.sleep(1.5)
        _paste(ui)
        time.sleep(0.4)
        _submit_composer(ui)
    finally:
        ui.close()
        _release_clipboard(clip)
    return {
        "ok": True,
        "steps": ["clipboard", "ctrl_t", "wait", "paste", "ctrl_enter"],
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
    """Focus the Glass window, then ctrl-/ (or palette). Not the IDE title."""
    _require_display()
    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "opener": opener,
            "query": query,
            "window": "glass",
        }
    chosen = _pick_cursor_window("glass", None, repo)
    _focus_window(str(chosen["title"]), "cursor")
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
    chosen = _pick_cursor_window("glass", None, repo)
    _focus_window(str(chosen["title"]), "cursor")
    ui = _ui()
    try:
        _quick_command(ui, "composer", opener="ctrl_slash")
        _quick_command(ui, model_query, opener="ctrl_slash")
    finally:
        ui.close()
    return {"ok": True, "model_query": model_query}


def main() -> int:
    """CLI for IDE launch, same-tab followup, and Glass quick-command probes.

    ``launch`` sends Ctrl+T on the IDE. ``--raise-uri`` is refused because
    Firefox owns ``vscode-remote://``. Prints a JSON verdict.
    """
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser(
        "launch", help="Focus the IDE, Ctrl+T (new tab), paste, Ctrl+Enter"
    )
    lp.add_argument("--message", help="First user message (e.g. resume 10223 …)")
    lp.add_argument(
        "--message-file", help="Read message from file (preferred for multiline)"
    )
    lp.add_argument(
        "--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO)
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
        help="Refused. vscode-remote:// is owned by Firefox; use --focus-title.",
    )
    lp.add_argument(
        "--focus-title",
        default=None,
        help="IDE window title substring only. Glass is a different title; "
        "do not pass the Glass title here.",
    )
    lp.add_argument(
        "--focus-app-id",
        default="cursor",
        help="app_id substring the focused toplevel must carry (default: cursor)",
    )
    fp = sub.add_parser(
        "followup",
        help="Focus Cursor, paste into the live composer, Ctrl+Enter (no new tab)",
    )
    fp.add_argument("--message", help="Follow-up user message (e.g. WAKE induction block)")
    fp.add_argument(
        "--message-file", help="Read message from file (preferred for multiline)"
    )
    fp.add_argument(
        "--repo", default=os.environ.get("ORCHESTRATOR_REPO", _DEFAULT_REPO)
    )
    fp.add_argument("--dry-run", action="store_true")
    fp.add_argument(
        "--no-raise",
        action="store_true",
        help="Do not run `cursor -r <repo>` first (paste into whatever window is focused)",
    )
    fp.add_argument(
        "--focus-title",
        default=None,
        help="IDE window title substring. Glass uses a different title and is not selected here.",
    )
    fp.add_argument(
        "--focus-app-id",
        default="cursor",
        help="app_id substring the focused toplevel must carry (default: cursor)",
    )
    gq = sub.add_parser(
        "glass-cmd",
        help="Focus the Glass window (title contains Glass, not the IDE title), then ctrl-/",
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
        if args.raise_uri:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "phase": "raise_uri_refused",
                        "fix": "use --focus-title (compositor activate); "
                        "vscode-remote:// / --folder-uri goes to Firefox",
                    }
                )
            )
            return 2
        out = launch_new_chat_with_message(
            message,
            repo=args.repo,
            dry_run=args.dry_run,
            raise_window=not args.no_raise,
            focus_title=args.focus_title,
            focus_app_id=args.focus_app_id,
        )
        import json

        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "followup":
        if args.message_file:
            message = Path(args.message_file).read_text(encoding="utf-8").strip()
        elif args.message:
            message = args.message.strip()
        else:
            raise SystemExit("followup requires --message or --message-file")
        out = followup_existing_chat_with_message(
            message,
            repo=args.repo,
            dry_run=args.dry_run,
            raise_window=not args.no_raise,
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
