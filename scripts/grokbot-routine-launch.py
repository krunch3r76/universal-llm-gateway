#!/usr/bin/env python3
"""SSH to orion-node and keystroke-trigger a Grok Bot treasury routine cycle.

Replaces broken Grok Bot app Routine timer — same hopper pattern as
orchestrator-tab-handoff.py (io → SSH → graphical host uinput paste).

Usage:
  scripts/grokbot-routine-launch.py launch
  scripts/grokbot-routine-launch.py launch --dry-run
  scripts/grokbot-routine-launch.py launch --message-file tmp/prompts/grok-treasury-routine-start.md
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
from pathlib import Path

from durable_io.atomic import durable_write_text

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_SSH_HOST = os.environ.get("GROKBOT_SSH_HOST", "orion-node")
_DEFAULT_REPO_REMOTE = os.environ.get("GROKBOT_REPO", str(_REPO))
_DEFAULT_MESSAGE = _REPO / "tmp/prompts/grok-treasury-routine-start.md"
_KEYSTROKE = "scripts/grokbot_tab_keystroke.py"
_MSG_DIR = _REPO / "tmp/watchers/grokbot-routine-messages"
_STATE = _REPO / "tmp/watchers/grokbot-routine-launch.state.json"


def _write_state(payload: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    durable_write_text(_STATE, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def launch(
    *,
    message_file: Path | None = None,
    ssh_host: str = _DEFAULT_SSH_HOST,
    remote_repo: str = _DEFAULT_REPO_REMOTE,
    dry_run: bool = False,
) -> dict:
    mf = message_file or _DEFAULT_MESSAGE
    if not mf.is_file():
        return {"ok": False, "reason": "message_file_missing", "path": str(mf)}

    cooldown_s = int(os.environ.get("GROKBOT_ROUTINE_COOLDOWN_S", "120"))
    if cooldown_s > 0 and _STATE.is_file() and not dry_run:
        try:
            prev = json.loads(_STATE.read_text(encoding="utf-8"))
            if prev.get("ok") and prev.get("updated_at"):
                from datetime import datetime, timezone

                prev_at = datetime.strptime(str(prev["updated_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                age_s = time.time() - prev_at.timestamp()
                if age_s < cooldown_s:
                    return {
                        "ok": True,
                        "skipped": True,
                        "reason": "cooldown",
                        "cooldown_s": cooldown_s,
                        "age_s": round(age_s, 1),
                        "holder": prev.get("holder"),
                    }
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    holder = f"grokbot-routine-{uuid.uuid4().hex[:10]}"
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    local_copy = _MSG_DIR / f"{holder}.md"
    durable_write_text(local_copy, mf.read_text(encoding="utf-8"))

    remote_msg = f"{remote_repo}/tmp/watchers/grokbot-routine-messages/{holder}.md"
    remote_script = f"{remote_repo}/{_KEYSTROKE}"
    cmd = (
        f"export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"mkdir -p {shlex.quote(remote_repo)}/tmp/watchers/grokbot-routine-messages; "
        f"python3 {shlex.quote(remote_script)} launch "
        f"--message-file {shlex.quote(remote_msg)}"
    )
    if dry_run:
        out = {
            "ok": True,
            "dry_run": True,
            "holder": holder,
            "local_message": str(local_copy),
            "ssh_host": ssh_host,
            "remote_cmd": cmd,
        }
        _write_state(out)
        return out

    scp = subprocess.run(
        ["scp", str(local_copy), f"{ssh_host}:{remote_msg}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if scp.returncode != 0:
        out = {"ok": False, "phase": "scp", "stderr": scp.stderr, "stdout": scp.stdout}
        _write_state(out)
        return out

    try:
        proc = subprocess.run(
            ["ssh", ssh_host, cmd],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        out = {"ok": False, "phase": "ssh_timeout", "holder": holder}
        _write_state(out)
        return out

    if proc.returncode != 0:
        out = {
            "ok": False,
            "phase": "keystroke",
            "holder": holder,
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "returncode": proc.returncode,
        }
        _write_state(out)
        return out

    try:
        keystroke_out = json.loads(proc.stdout.strip().split("\n")[-1])
    except json.JSONDecodeError:
        keystroke_out = {"raw_stdout": proc.stdout}

    out = {"ok": True, "holder": holder, "keystroke": keystroke_out}
    _write_state(out)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("launch")
    lp.add_argument("--message-file", type=Path, default=None)
    lp.add_argument("--ssh-host", default=_DEFAULT_SSH_HOST)
    lp.add_argument("--remote-repo", default=_DEFAULT_REPO_REMOTE)
    lp.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    out = launch(
        message_file=args.message_file,
        ssh_host=args.ssh_host,
        remote_repo=args.remote_repo,
        dry_run=args.dry_run,
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
