#!/usr/bin/env python3
"""Orchestrator tab handoff lock — single-writer gate for resume/checkpoint cycles.

Prevents double-resume / double-WORK when keystroke automation opens a fresh
Cursor tab while heartbeat or mobile might also fire.

Lock path: tmp/watchers/orchestrator-handoff.lock (JSON)

Protocol (operator / automation — not implemented here):
  1. Keystroke opens new Cursor tab → paste ``resume 10223`` (or bound prompt)
  2. First action in tab: ``acquire`` (or fail closed if lock held)
  3. Run bounded job (WORK subagent, consolidation audit, …)
  4. Post CHECKPOINT on 10223
  5. ``release`` after CHECKPOINT lands (same turn)

Heartbeat / subagent launch MUST call ``assert_idle()`` before spawning WORK.

Usage:
  python scripts/orchestrator-tab-handoff.py status
  python scripts/orchestrator-tab-handoff.py acquire --intent consolidation-audit --holder tab-abc
  python scripts/orchestrator-tab-handoff.py release --holder tab-abc
  python scripts/orchestrator-tab-handoff.py release --force   # operator recovery only

Spec: tmp/prompts/orchestrator-tab-handoff-protocol.md

Launch (orion-node Wayland keystrokes):
  python scripts/orchestrator-tab-handoff.py launch --intent consolidation-audit \\
    --work-prompt tmp/prompts/tab-launch-work-consolidation-quality-audit-10223-2026-09-08.md
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
from typing import Any

from durable_io.atomic import durable_write_text

try:
    from orchestrator_handoff.queue import HandoffQueue, default_queue_path
    from orchestrator_handoff.work_prompt import (
        WorkClass,
        classify_work_prompt,
        work_execution_lines,
    )
except ImportError:  # pragma: no cover — script invoked without libs on path
    HandoffQueue = None  # type: ignore[misc, assignment]
    default_queue_path = None  # type: ignore[misc, assignment]
    WorkClass = None  # type: ignore[misc, assignment]
    classify_work_prompt = None  # type: ignore[misc, assignment]
    work_execution_lines = None  # type: ignore[misc, assignment]

_REPO = Path(__file__).resolve().parents[1]
_LOCK = _REPO / "tmp/watchers/orchestrator-handoff.lock"
_DEFAULT_STALE_S = 4 * 3600  # 4h — recovery if tab crashed mid-job
_DEFAULT_SSH_HOST = os.environ.get("ORCHESTRATOR_SSH_HOST", "orion-node")
_DEFAULT_REPO_REMOTE = os.environ.get("ORCHESTRATOR_REPO", str(_REPO))
_KEYSTROKE = _REPO / "scripts/orchestrator_tab_keystroke.py"
_HANDOFF_MSG_DIR = _REPO / "tmp/watchers/handoff-messages"


def _read() -> dict[str, Any] | None:
    if not _LOCK.is_file():
        return None
    try:
        data = json.loads(_LOCK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"corrupt": True, "path": str(_LOCK)}
    return data if isinstance(data, dict) else None


def _write(payload: dict[str, Any]) -> dict[str, Any]:
    _LOCK.parent.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    durable_write_text(_LOCK, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _is_stale(data: dict[str, Any], stale_s: float) -> bool:
    started = data.get("acquired_at") or data.get("updated_at")
    if not started:
        return True
    try:
        # naive UTC parse
        from datetime import UTC, datetime

        t0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        age = (datetime.now(UTC) - t0).total_seconds()
        return age > stale_s
    except (TypeError, ValueError):
        return True


def status(stale_s: float = _DEFAULT_STALE_S) -> dict[str, Any]:
    data = _read()
    if not data:
        return {"held": False, "path": str(_LOCK)}
    stale = _is_stale(data, stale_s)
    return {"held": not stale, "stale": stale, "lock": data, "path": str(_LOCK)}


def acquire(
    *,
    holder: str,
    intent: str,
    thread: str = "10223",
    stale_s: float = _DEFAULT_STALE_S,
    queue_id: str | None = None,
) -> dict[str, Any]:
    cur = _read()
    if cur and not _is_stale(cur, stale_s):
        return {
            "ok": False,
            "reason": "lock_held",
            "holder": cur.get("holder"),
            "intent": cur.get("intent"),
            "acquired_at": cur.get("acquired_at"),
        }
    payload = {
        "holder": holder,
        "intent": intent,
        "thread": thread,
        "pid": os.getpid(),
        "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if queue_id:
        payload["queue_id"] = queue_id
    _write(payload)
    return {"ok": True, "lock": payload}


def release(
    holder: str | None = None,
    force: bool = False,
    *,
    closeout_turn: int | None = None,
    outcome: str = "done",
) -> dict[str, Any]:
    cur = _read()
    if not cur:
        return {"ok": True, "reason": "no_lock"}
    if not force and holder and cur.get("holder") != holder:
        return {
            "ok": False,
            "reason": "holder_mismatch",
            "expected": holder,
            "actual": cur.get("holder"),
        }
    queue_id = cur.get("queue_id")
    _LOCK.unlink(missing_ok=True)
    result: dict[str, Any] = {"ok": True, "released": cur}
    if queue_id and HandoffQueue is not None:
        q = HandoffQueue.open()
        if outcome == "failed":
            q_out = q.fail(str(queue_id), "force_release")
        else:
            q_out = q.complete(
                item_id=str(queue_id),
                holder=holder or str(cur.get("holder") or ""),
                closeout_turn=closeout_turn,
            )
        result["queue_complete"] = q_out
    return result


def ack(holder: str) -> dict[str, Any]:
    cur = _read()
    if not cur:
        return {"ok": False, "reason": "no_lock"}
    if cur.get("holder") != holder:
        return {
            "ok": False,
            "reason": "holder_mismatch",
            "expected": holder,
            "actual": cur.get("holder"),
        }
    cur["acked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = _write(cur)
    return {"ok": True, "lock": payload}


def assert_idle(stale_s: float = _DEFAULT_STALE_S) -> None:
    st = status(stale_s=stale_s)
    if st.get("held"):
        raise SystemExit(
            f"orchestrator handoff lock held: {json.dumps(st.get('lock'), default=str)}"
        )


def _build_handoff_message(
    *,
    thread: str,
    holder: str,
    intent: str,
    work_prompt: Path | None,
    queue_id: str = "",
    work_class: str | None = None,
) -> str:
    if classify_work_prompt is not None and work_prompt is not None:
        wc = classify_work_prompt(work_prompt)
    elif work_class and WorkClass is not None:
        wc = WorkClass(work_class)
    elif WorkClass is not None:
        wc = WorkClass.MECHANICAL
    else:
        wc = None

    exec_lines = (
        work_execution_lines(work_class=wc, work_prompt=work_prompt)
        if work_execution_lines is not None and wc is not None
        else [
            (
                f"1. Read and execute: `{work_prompt}` — WORK in-seat."
                if work_prompt
                else "1. Follow heartbeat WORK_PENDING."
            )
        ]
    )

    qline = f" queue_id=`{queue_id}`" if queue_id else ""
    wc_line = f" work_class=`{wc.value}`" if wc is not None else ""

    parts = [
        f"resume {thread}\n",
        f"**ORCHESTRATOR_HANDOFF** holder=`{holder}` intent=`{intent}`{qline}{wc_line}",
        "Lock is pre-held on io — do not acquire again.",
        f"0. First action: `python scripts/orchestrator-tab-handoff.py ack --holder {holder}` "
        f"(re-run every ~60 min on long WORK).",
    ]
    parts.extend(exec_lines)
    parts.extend(
        [
            "2. If the WORK prompt requires it: CLOSEOUT on 10303.",
            f"3. Exactly one CHECKPOINT on {thread}; note turn N. "
            f"Rename tab `. {thread} human-continuity-speech-tape-design`.",
            f"4. Same turn: `python scripts/orchestrator-tab-handoff.py release "
            f"--holder {holder} --checkpoint-turn N`.",
            f"   (completes queue item `{queue_id}` when set)",
            "If queue empty after job: `python scripts/orchestrator-handoff-registrar.py discover`.",
        ]
    )
    return "\n".join(parts)


def launch_tab(
    *,
    intent: str,
    thread: str = "10223",
    holder: str | None = None,
    work_prompt: str | None = None,
    ssh_host: str = _DEFAULT_SSH_HOST,
    remote_repo: str = _DEFAULT_REPO_REMOTE,
    palette_query: str = "New Chat",
    dry_run: bool = False,
    stale_s: float = _DEFAULT_STALE_S,
    queue_id: str | None = None,
) -> dict[str, Any]:
    """Acquire lock on io, SSH to graphical host, keystroke new Cursor chat + paste handoff."""
    holder = holder or f"keystroke-{uuid.uuid4().hex[:12]}"
    wp: Path | None = None
    if work_prompt:
        wp = Path(work_prompt)
        if not wp.is_file():
            wp = _REPO / work_prompt
        if not wp.is_file():
            return {"ok": False, "reason": "work_prompt_missing", "path": work_prompt}

    if not dry_run:
        acq = acquire(
            holder=holder,
            intent=intent,
            thread=thread,
            stale_s=stale_s,
            queue_id=queue_id,
        )
        if not acq.get("ok"):
            return {"ok": False, "phase": "acquire", **acq}

    message = _build_handoff_message(
        thread=thread,
        holder=holder,
        intent=intent,
        work_prompt=wp,
        queue_id=queue_id or "",
    )
    _HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    msg_path = _HANDOFF_MSG_DIR / f"{holder}.md"
    durable_write_text(msg_path, message)

    remote_msg = f"{remote_repo}/tmp/watchers/handoff-messages/{holder}.md"
    remote_script = f"{remote_repo}/scripts/orchestrator_tab_keystroke.py"
    cmd = (
        f"export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(remote_script)} launch "
        f"--message-file {shlex.quote(remote_msg)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"--palette-query {shlex.quote(palette_query)}"
    )
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "holder": holder,
            "message_path": str(msg_path),
            "ssh_host": ssh_host,
            "remote_cmd": cmd,
        }

    try:
        proc = subprocess.run(
            ["ssh", ssh_host, cmd],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        release(holder=holder, force=True, outcome="failed")
        return {
            "ok": False,
            "phase": "ssh_timeout",
            "holder": holder,
        }
    if proc.returncode != 0:
        release(holder=holder, force=True, outcome="failed")
        return {
            "ok": False,
            "phase": "keystroke",
            "holder": holder,
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "returncode": proc.returncode,
        }
    try:
        keystroke_out = json.loads(proc.stdout.strip().split("\n")[-1])
    except json.JSONDecodeError:
        keystroke_out = {"raw_stdout": proc.stdout}
    return {
        "ok": True,
        "holder": holder,
        "intent": intent,
        "message_path": str(msg_path),
        "keystroke": keystroke_out,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    a = sub.add_parser("acquire")
    a.add_argument("--holder", required=True)
    a.add_argument("--intent", required=True)
    a.add_argument("--thread", default="10223")
    r = sub.add_parser("release")
    r.add_argument("--holder", default=None)
    r.add_argument("--force", action="store_true")
    r.add_argument("--checkpoint-turn", type=int, default=None)
    r.add_argument("--outcome", choices=("done", "failed"), default="done")
    ak = sub.add_parser("ack")
    ak.add_argument("--holder", required=True)
    sub.add_parser("assert-idle")
    lp = sub.add_parser("launch", help="Acquire lock + SSH keystroke new tab on orion-node")
    lp.add_argument("--intent", required=True)
    lp.add_argument("--thread", default="10223")
    lp.add_argument("--holder", default=None)
    lp.add_argument("--work-prompt", default=None, help="Path to WORK prompt file (repo-relative ok)")
    lp.add_argument("--ssh-host", default=_DEFAULT_SSH_HOST)
    lp.add_argument("--remote-repo", default=_DEFAULT_REPO_REMOTE)
    lp.add_argument("--palette-query", default="New Chat")
    lp.add_argument("--dry-run", action="store_true")
    lp.add_argument("--queue-id", default=None, help="Registrar queue item id")

    args = p.parse_args()
    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
    elif args.cmd == "acquire":
        out = acquire(holder=args.holder, intent=args.intent, thread=args.thread)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 2
    elif args.cmd == "release":
        print(
            json.dumps(
                release(
                    holder=args.holder,
                    force=args.force,
                    closeout_turn=args.checkpoint_turn,
                    outcome=args.outcome if args.force else "done",
                ),
                indent=2,
            )
        )
    elif args.cmd == "ack":
        out = ack(holder=args.holder)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 2
    elif args.cmd == "assert-idle":
        assert_idle()
        print(json.dumps({"idle": True}))
    elif args.cmd == "launch":
        out = launch_tab(
            intent=args.intent,
            thread=args.thread,
            holder=args.holder,
            work_prompt=args.work_prompt,
            ssh_host=args.ssh_host,
            remote_repo=args.remote_repo,
            palette_query=args.palette_query,
            dry_run=args.dry_run,
            queue_id=args.queue_id,
        )
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
