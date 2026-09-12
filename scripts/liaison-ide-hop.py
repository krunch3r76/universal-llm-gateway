#!/usr/bin/env python3
"""Hop the attended liaison IDE tab: keystroke ``resume <R>`` into a fresh Cursor chat.

Run after the CHECKPOINT lands (same turn), as the last action of the old tab:

  python scripts/liaison-ide-hop.py --root 10479 --row "R16 GPT removal densify" [--dry-run]
  python scripts/liaison-ide-hop.py --root 10479 --find-transcript "resume 10479"

The message the new tab receives is ``resume <R>`` plus NOW row and one ARM line per
live poller (auto-discovered from tmp/watchers, or ``--arm LABEL`` explicitly); the
successor re-arms those tails, harvests, and plans. ``--find-transcript`` prints the
transcript id of the tab whose first user message contains the text — the value the
common checkpoint needs (``continuity(op=checkpoint, surface=cursor, transcript_id=...)``).

Substrate: libs/bus_watch/ide_hop.py (message + SSH keystroke on the GUI host) and
scripts/orchestrator_tab_keystroke.py (evdev/Wayland, runs on the GUI host over NFS).
"""

from __future__ import annotations

import argparse
import json
import sys

from bus_watch.ide_hop import (
    DEFAULT_GUI_HOST,
    DEFAULT_REMOTE_REPO,
    build_ide_hop_message,
    find_transcript_id,
    fire_ide_hop,
    live_watcher_labels,
)


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
    p.add_argument("--gui-host", default=DEFAULT_GUI_HOST)
    p.add_argument("--remote-repo", default=DEFAULT_REMOTE_REPO)
    p.add_argument("--palette-query", default="New Chat")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="write the message, print the remote command, no keys",
    )
    p.add_argument(
        "--find-transcript",
        metavar="TEXT",
        help="print the transcript id whose first user turn contains TEXT",
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
            lbl for lbl in live_watcher_labels(args.root) if lbl not in labels
        )
    message = build_ide_hop_message(
        args.root, row=args.row, arm_labels=labels, tip_cp_ordinal=args.tip_cp
    )
    out = fire_ide_hop(
        message,
        root_id=args.root,
        gui_host=args.gui_host,
        remote_repo=args.remote_repo,
        palette_query=args.palette_query,
        dry_run=args.dry_run,
    )
    out["arm_labels"] = labels
    out["message"] = message
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
