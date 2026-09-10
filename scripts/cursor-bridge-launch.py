#!/usr/bin/env python3
"""io → SSH → jupiter keystroke relay for the Cursor keystroke bridge.

Same hopper shape as ``grokbot-routine-launch.py`` minus scp: the repo is one
NFS mount, so the message file written here is already visible on the
graphical host. Ops mirror ``cursor_tab_keystroke.py``:

  open-tab  --thread T --slug S [--cowork-url U] [--message-file F]
  paste     --thread T --message M | --message-file F [--focus-title "T S"]
  status

Per-thread cooldown on ``open-tab`` prevents duplicate tabs on crash-restart
(2026-09-09 Grok hopper lesson). State: tmp/watchers/cursor-bridge-launch.state.json.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from durable_io.atomic import durable_write_text

_REPO = Path(__file__).resolve().parents[1]
_SSH_HOST = os.environ.get("CURSOR_BRIDGE_SSH_HOST", "jupiter")
_REMOTE_ENV = os.environ.get(
    "CURSOR_BRIDGE_REMOTE_ENV",
    "WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000",
)
_KEYSTROKE = _REPO / "scripts/cursor_tab_keystroke.py"
_MSG_DIR = _REPO / "tmp/watchers/cursor-bridge-messages"
_STATE = _REPO / "tmp/watchers/cursor-bridge-launch.state.json"
_OPEN_TEMPLATE = _REPO / "tmp/prompts/cursor-bridge-open.md"
_OPEN_COOLDOWN_S = int(os.environ.get("CURSOR_BRIDGE_OPEN_COOLDOWN_S", "300"))
_TS = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> str:
    return time.strftime(_TS, time.gmtime())


def _read_state() -> dict:
    if not _STATE.is_file():
        return {}
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    durable_write_text(_STATE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _record(op: str, thread: str, result: dict) -> dict:
    state = _read_state()
    state.setdefault("threads", {}).setdefault(thread, {})[op] = {
        **result,
        "at": _now(),
    }
    state["last"] = {"op": op, "thread": thread, "ok": result.get("ok")}
    _write_state(state)
    return result


def _open_cooldown_hit(thread: str) -> dict | None:
    prev = (_read_state().get("threads", {}).get(thread, {}) or {}).get(
        "open-tab"
    ) or {}
    if not (prev.get("ok") and prev.get("at")) or _OPEN_COOLDOWN_S <= 0:
        return None
    prev_at = datetime.strptime(str(prev["at"]), _TS).replace(tzinfo=UTC)
    age_s = time.time() - prev_at.timestamp()
    if age_s >= _OPEN_COOLDOWN_S:
        return None
    return {
        "ok": False,
        "skipped": True,
        "reason": "cooldown",
        "phase": "cooldown",
        "cooldown_s": _OPEN_COOLDOWN_S,
        "age_s": round(age_s, 1),
        "holder": prev.get("holder"),
    }


def _stage_message(text: str, holder: str) -> Path:
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    path = _MSG_DIR / f"{holder}.md"
    durable_write_text(path, text if text.endswith("\n") else text + "\n")
    return path


def _ssh_keystroke(argv: list[str], *, holder: str, dry_run: bool) -> dict:
    remote = (
        f"export {_REMOTE_ENV}; python3 {shlex.quote(str(_KEYSTROKE))} "
        + " ".join(shlex.quote(a) for a in argv)
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "holder": holder,
            "ssh_host": _SSH_HOST,
            "remote_cmd": remote,
        }
    try:
        proc = subprocess.run(
            ["ssh", _SSH_HOST, remote], capture_output=True, text=True, timeout=90
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "ssh_timeout", "holder": holder}
    if proc.returncode != 0:
        return {
            "ok": False,
            "phase": "keystroke",
            "holder": holder,
            "returncode": proc.returncode,
            "stderr": proc.stderr[-800:],
            "stdout": proc.stdout[-800:],
        }
    try:
        keystroke = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return {
            "ok": False,
            "phase": "keystroke_json",
            "holder": holder,
            "stdout": proc.stdout[-800:],
            "stderr": proc.stderr[-800:],
        }
    ok = bool(keystroke.get("ok", True))
    out: dict = {"ok": ok, "holder": holder, "keystroke": keystroke}
    if not ok:
        out["reason"] = keystroke.get("reason", "keystroke_failed")
        out["phase"] = "keystroke"
    return out


def render_open_message(
    *, thread: str, slug: str, cowork_url: str, template: Path | None = None
) -> str:
    tpl = (template or _OPEN_TEMPLATE).read_text(encoding="utf-8")
    return (
        tpl.replace("{thread}", thread)
        .replace("{slug}", slug)
        .replace("{cowork_url}", cowork_url or "(none — reply on bus only)")
    )


def open_tab(
    *,
    thread: str,
    slug: str,
    cowork_url: str = "",
    message_file: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    if not force and not dry_run:
        hit = _open_cooldown_hit(thread)
        if hit:
            return hit
    text = (
        message_file.read_text(encoding="utf-8")
        if message_file
        else render_open_message(thread=thread, slug=slug, cowork_url=cowork_url)
    )
    holder = f"bridge-open-{thread}-{uuid.uuid4().hex[:8]}"
    path = _stage_message(text, holder)
    argv = ["open", "--message-file", str(path)]
    if dry_run:
        argv.append("--dry-run")
    out = _ssh_keystroke(argv, holder=holder, dry_run=dry_run)
    out.update(
        {
            "thread": thread,
            "slug": slug,
            "message_path": str(path),
            "title": f"{thread} {slug}",
        }
    )
    return _record("open-tab", thread, out)


def paste(
    *,
    thread: str,
    message: str,
    focus_title: str = "",
    focus_opener: str | None = None,
    input_focus: str | None = None,
    dry_run: bool = False,
) -> dict:
    holder = f"bridge-paste-{thread}-{uuid.uuid4().hex[:8]}"
    path = _stage_message(message, holder)
    argv = ["paste", "--message-file", str(path)]
    if focus_title:
        argv += ["--focus-title", focus_title]
    if focus_opener:
        argv += ["--focus-opener", focus_opener]
    if input_focus:
        argv += ["--input-focus", input_focus]
    if dry_run:
        argv.append("--dry-run")
    out = _ssh_keystroke(argv, holder=holder, dry_run=dry_run)
    out.update({"thread": thread, "message_path": str(path)})
    return _record("paste", thread, out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ot = sub.add_parser("open-tab")
    ot.add_argument("--thread", required=True)
    ot.add_argument("--slug", required=True)
    ot.add_argument("--cowork-url", default="")
    ot.add_argument(
        "--message-file", type=Path, default=None, help="Override rendered template"
    )
    ot.add_argument(
        "--force", action="store_true", help="Bypass per-thread open cooldown"
    )
    ot.add_argument("--dry-run", action="store_true")

    ps = sub.add_parser("paste")
    ps.add_argument("--thread", required=True)
    ps.add_argument("--message")
    ps.add_argument("--message-file", type=Path)
    ps.add_argument("--focus-title", default="")
    ps.add_argument("--focus-opener", choices=("none", "ctrl_slash", "ctrl_shift_p"))
    ps.add_argument("--input-focus", choices=("ctrl_l", "none"))
    ps.add_argument("--dry-run", action="store_true")

    sub.add_parser("status")

    args = p.parse_args()
    if args.cmd == "status":
        out = _read_state()
    elif args.cmd == "open-tab":
        out = open_tab(
            thread=args.thread,
            slug=args.slug,
            cowork_url=args.cowork_url,
            message_file=args.message_file,
            force=args.force,
            dry_run=args.dry_run,
        )
    else:
        if args.message_file:
            message = args.message_file.read_text(encoding="utf-8").strip()
        elif args.message:
            message = args.message.strip()
        else:
            raise SystemExit("paste requires --message or --message-file")
        out = paste(
            thread=args.thread,
            message=message,
            focus_title=args.focus_title,
            focus_opener=args.focus_opener,
            input_focus=args.input_focus,
            dry_run=args.dry_run,
        )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
