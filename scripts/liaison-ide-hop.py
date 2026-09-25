#!/usr/bin/env python3
"""Hop the attended liaison IDE tab: keystroke ``resume <R>`` into a fresh Cursor chat.

Run after the CHECKPOINT lands (same turn), as the last action of the old tab:

  python scripts/liaison-ide-hop.py --root 10479 --row "R16 GPT removal densify" \\
    --transcript-id <departing-tab-uuid> [--dry-run]
  python scripts/liaison-ide-hop.py --root 10479 --find-transcript "resume 10479"

The message the new tab receives is ``resume <R>`` plus NOW row and one ARM tail attach
line per live poller (auto-discovered from tmp/watchers, or ``--arm LABEL`` explicitly);
the successor attaches ``tail --label``, harvests, and plans. ``ok`` then retires this
tab's ``--loop``, ``watch-supervise`` tails, and ``ide:`` lock (``bus_watch.ide_hop_retire``).
UpdateGoal only if a leftover native goal is active.
``--find-transcript`` prints the transcript id of the tab whose first user message
contains the text — the value the common checkpoint needs
(``continuity(op=checkpoint, surface=cursor, transcript_id=...)``).

Substrate: libs/bus_watch/ide_hop.py (message + SSH keystroke on the GUI host),
libs/bus_watch/ide_hop_retire.py (departing-tab teardown), and
scripts/orchestrator_tab_keystroke.py (evdev/Wayland, runs on the GUI host over NFS).
"""

from __future__ import annotations

import argparse
import json
import sys

from bus_watch.digest_budget import _bus, _get, effective_policy
from bus_watch.fable_lock import WATCH_DIR
from bus_watch.hop_qualify import hop_qualifies
from bus_watch.ide_hop import (
    DEFAULT_REMOTE_REPO,
    build_ide_hop_message,
    find_transcript_id,
    fire_ide_hop,
    live_watcher_labels,
    policy_gui_host,
    seal_hop_window,
    tick_register,
)
from bus_watch.ide_hop_retire import GOAL_RELEASE, retire_departing_tab
from bus_watch.judgment_rows import harvest_judgment_turns
from bus_watch.liaison_digest import build_digest
from bus_watch.now_row import format_now_line, resolve_now_row
from bus_watch.tick_state import load_state


def _post_root_checkpoint(root_id: str, body: str) -> bool:
    # agent-bus posts turns at POST /turns with thread in the body — not
    # /threads/{id}/turns (that path 404s and blocked CONTEXT_BUDGET hops).
    with _bus() as client:
        r = client.post(
            "/turns",
            json={
                "thread": root_id,
                "from": "cursor",
                "to": "cursor",
                "subject": f"HARVEST judgment — {root_id}",
                "body": body,
            },
        )
        return r.status_code < 400


def _mark_turns_read(root_id: str, turn_numbers: list[int]) -> None:
    # OpenAPI: PATCH /threads/{thread_id}/turns/read-state (hyphen).
    with _bus() as client:
        client.patch(
            f"/threads/{root_id}/turns/read-state",
            json={"turn_numbers": turn_numbers, "agent": "cursor"},
        )


def _fetch_turns(root_id: str, **params: object) -> dict:
    with _bus() as client:
        return _get(client, "/turns", thread=root_id, **params) or {}


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--root", default="10479", help="continuity root thread id")
    p.add_argument("--row", help="scoreboard NOW row the successor picks up")
    p.add_argument(
        "--arm",
        action="append",
        default=[],
        help="watcher label to re-arm (repeatable)",
    )
    p.add_argument(
        "--no-auto-arm",
        action="store_true",
        help="do not add live pollers found in tmp/watchers",
    )
    p.add_argument(
        "--tip-cp",
        type=int,
        default=None,
        help="tip CHECKPOINT ordinal, for the message header",
    )
    p.add_argument(
        "--transcript-id",
        default=None,
        metavar="UUID",
        help="departing tab transcript id — required; seals channel=hop before keystroke",
    )
    p.add_argument(
        "--gui-host",
        default=None,
        help="override policy.gui_host for this hop only (default: liaison-tick.py --set gui_host=…)",
    )
    p.add_argument("--remote-repo", default=DEFAULT_REMOTE_REPO)
    p.add_argument(
        "--no-raise",
        action="store_true",
        help="type into the window the operator has focused; skip compositor activate",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="write the message, print the remote command, no keys",
    )
    p.add_argument(
        "--find-transcript",
        metavar="TEXT",
        help="print the transcript id whose first user turn contains TEXT "
        "(among matches, highest tip_cp= wins; pass tip_cp=N when known)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="hop even when hop_qualifies refuses (operator override)",
    )
    p.add_argument(
        "--exclude-lane",
        action="append",
        default=[],
        metavar="THREAD",
        help="ignore watchers on THREAD — pass the calling seat's own dispatch "
        "lane so its pending closeout does not count as follow-up (repeatable)",
    )
    args = p.parse_args()

    if args.find_transcript:
        tid = find_transcript_id(args.find_transcript)
        print(json.dumps({"ok": tid is not None, "transcript_id": tid}))
        return 0 if tid else 2

    if not args.row:
        p.error("--row is required for a hop")
    labels = list(args.arm)
    if not args.no_auto_arm:
        labels.extend(
            lbl
            for lbl in live_watcher_labels(args.root, exclude_threads=args.exclude_lane)
            if lbl not in labels
        )
    if not args.transcript_id:
        print(
            json.dumps(
                {
                    "ok": False,
                    "phase": "seal_transcript_unknown",
                    "root": args.root,
                    "fix": "pass --transcript-id <departing tab uuid>",
                }
            )
        )
        return 2
    state_path = WATCH_DIR / f"liaison-{args.root}.tick.json"
    state = load_state(state_path)
    policy = effective_policy(state)
    now_row_set_at = state.get("now_row_set_at")
    harvest = harvest_judgment_turns(
        args.root,
        get_turns=_fetch_turns,
        post_checkpoint=_post_root_checkpoint,
        mark_read=_mark_turns_read,
        now_row_set_at=str(now_row_set_at or "") or None,
    )
    if not harvest.get("ok"):
        print(json.dumps({"ok": False, "phase": "harvest_failed", **harvest}))
        return 2
    digest = build_digest(
        args.root,
        state,
        register=str(state.get("register") or "attended"),
        budget_tokens=int(policy.get("ide_window_tokens") or 256_000),
    )
    raw_row, row_source = resolve_now_row(digest)
    row = format_now_line(raw_row or args.row, row_source, digest, omit_tip_prefix=True)
    if not row.strip():
        row = args.row
    qualify = hop_qualifies(row=row, arm_labels=labels, policy=policy)
    if not qualify["ok"] and not args.force:
        print(
            json.dumps(
                {
                    "ok": False,
                    "phase": "no_autonomous_followup",
                    "stay": True,
                    "root": args.root,
                    "harvest": harvest,
                    **qualify,
                }
            )
        )
        return 2
    seal = seal_hop_window(
        args.root,
        transcript_id=args.transcript_id,
    )
    if not seal.get("ok"):
        print(json.dumps({"ok": False, "root": args.root, **seal}, indent=2))
        return 2
    message = build_ide_hop_message(
        args.root,
        row=row,
        arm_labels=labels,
        tip_cp_ordinal=args.tip_cp,
        register=tick_register(args.root),
    )
    out = fire_ide_hop(
        message,
        root_id=args.root,
        gui_host=args.gui_host or policy_gui_host(args.root),
        remote_repo=args.remote_repo,
        dry_run=args.dry_run,
        no_raise=args.no_raise,
    )
    out["arm_labels"] = labels
    out["seal"] = seal
    out["message"] = message
    out["harvest"] = harvest
    out["row_source"] = row_source
    if out.get("ok"):
        out["goal_release"] = GOAL_RELEASE
        if args.dry_run:
            out["retire"] = {"ok": True, "skipped": "dry_run"}
        else:
            out["retire"] = retire_departing_tab(
                args.root,
                holder=f"ide:{args.transcript_id}",
                transcript_id=args.transcript_id,
            )
    print(json.dumps(out, indent=2))
    if out.get("ok"):
        # Seat obligation — not inferable from JSON alone in long hop turns.
        print(GOAL_RELEASE, file=sys.stderr)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
