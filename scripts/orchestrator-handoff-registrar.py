#!/usr/bin/env python3
"""Registrar CLI for orchestrator IDE handoff queue.

Enqueue WORK hops; heartbeat or ``dispatch-next`` keystroke-opens a fresh tab.

Usage:
  # Register work (hop in IDE later via keystroke dispatch):
  scripts/orchestrator-handoff-registrar.py enqueue \\
    --intent claudeburst-o16 --work-prompt tmp/prompts/tab-launch-work-....md --priority P0

  # Import **ready** rows from tab-launch index:
  scripts/orchestrator-handoff-registrar.py import-index

  # List / peek / one-shot dispatch:
  scripts/orchestrator-handoff-registrar.py list
  scripts/orchestrator-handoff-registrar.py peek
  scripts/orchestrator-handoff-registrar.py dispatch-next [--dry-run]

  # Tab completion (also auto on handoff release when lock carries queue_id):
  scripts/orchestrator-handoff-registrar.py complete --holder keystroke-abc
  scripts/orchestrator-handoff-registrar.py fail --id q-abc --reason "..."

Queue: tmp/watchers/orchestrator-handoff-queue-10223.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from orchestrator_handoff.queue import HandoffQueue, default_queue_path

_REPO = Path(__file__).resolve().parents[1]
_INDEX = _REPO / "tmp/prompts/tab-launch-index-10223.md"
_OPPORTUNITIES = _REPO / "tmp/prompts/10223-opportunities.md"
if not _OPPORTUNITIES.is_file():
    _OPPORTUNITIES = _REPO / "cortex/notes/system/threads/10223-opportunities.md"
_HANDOFF = _REPO / "scripts/orchestrator-tab-handoff.py"


def _dispatch_next(q: HandoffQueue, *, dry_run: bool) -> dict:
    item = q.peek()
    if not item:
        return {"ok": False, "reason": "queue_empty"}
    if dry_run:
        return {"ok": True, "dry_run": True, "item": item}
    q.mark_launching(item["id"])
    cmd = [
        sys.executable,
        str(_HANDOFF),
        "launch",
        "--intent",
        item["intent"],
        "--thread",
        str(item.get("thread") or "10223"),
        "--work-prompt",
        str(item.get("work_prompt") or ""),
        "--queue-id",
        item["id"],
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out = {"stdout": proc.stdout, "stderr": proc.stderr}
    if proc.returncode != 0 or not out.get("ok"):
        q.requeue_launch_failure(
            item["id"],
            str(out.get("reason") or out.get("phase") or proc.stderr[:200] or "launch_failed"),
        )
        return {"ok": False, "item_id": item["id"], "launch": out, "returncode": proc.returncode}
    holder = out.get("holder")
    if holder:
        q.mark_in_flight(item["id"], holder)
    return {"ok": True, "item_id": item["id"], "holder": holder, "launch": out}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--house", default="10223")
    p.add_argument("--queue-file", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enqueue")
    e.add_argument("--intent", required=True)
    e.add_argument("--work-prompt", required=True)
    e.add_argument("--priority", default="P2")
    e.add_argument("--notes", default="")
    e.add_argument("--thread", default="10223")

    lp = sub.add_parser("list")
    lp.add_argument("--status", default=None)

    sub.add_parser("peek")
    sub.add_parser("import-index")
    disc = sub.add_parser("discover")
    disc.add_argument("--enqueue-opportunities", action="store_true")
    d = sub.add_parser("dispatch-next")
    d.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("complete")
    c.add_argument("--id", default=None)
    c.add_argument("--holder", default=None)
    c.add_argument("--closeout-turn", type=int, default=None)

    f = sub.add_parser("fail")
    f.add_argument("--id", required=True)
    f.add_argument("--reason", required=True)

    x = sub.add_parser("cancel")
    x.add_argument("--id", required=True)

    args = p.parse_args()
    path = Path(args.queue_file) if args.queue_file else default_queue_path(args.house)
    q = HandoffQueue.open(house=args.house, path=path)

    if args.cmd == "enqueue":
        out = q.enqueue(
            intent=args.intent,
            work_prompt=args.work_prompt,
            priority=args.priority,
            notes=args.notes,
            thread=args.thread,
        )
    elif args.cmd == "list":
        out = {"items": q.list_items(status=args.status)}
    elif args.cmd == "peek":
        out = {"peek": q.peek(), "active": q.active_item()}
    elif args.cmd == "import-index":
        out = q.import_ready_index_rows(_INDEX)
    elif args.cmd == "discover":
        out = q.discover_work(
            index_path=_INDEX,
            opportunities_path=_OPPORTUNITIES if _OPPORTUNITIES.is_file() else None,
            enqueue_opportunities=args.enqueue_opportunities,
        )
    elif args.cmd == "dispatch-next":
        out = _dispatch_next(q, dry_run=args.dry_run)
    elif args.cmd == "complete":
        out = q.complete(args.id, holder=args.holder, closeout_turn=args.closeout_turn)
    elif args.cmd == "fail":
        out = q.fail(args.id, args.reason)
    elif args.cmd == "cancel":
        out = q.cancel(args.id)
    else:
        return 1

    print(json.dumps(out, indent=2))
    if args.cmd in ("list", "peek", "discover"):
        return 0
    if out.get("ok"):
        return 0
    if args.cmd == "dispatch-next" and out.get("reason") == "queue_empty":
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
