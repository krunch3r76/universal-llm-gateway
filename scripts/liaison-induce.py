#!/usr/bin/env python3
"""Plant digest induction into the lock-holder IDE tab (R10a follow-up keystroke).

  python scripts/liaison-induce.py --root 10479 --dry-run
  python scripts/liaison-induce.py --root 10479 --fire

Builds the same digest as ``liaison-tick.py --once``, takes ``induction``, and
pastes it into the live Cursor composer (focus → paste → Ctrl+Enter, no hop).
Never fires more than once per digest ``fingerprint`` (M5).

When ``register`` is not ``attended``, the CDP navigator transport renders the
static doorbell and submits via ``submit_team_dispatch`` instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.ide_followup import fire_ide_followup
from bus_watch.ide_hop import DEFAULT_REMOTE_REPO, policy_gui_host
from bus_watch.liaison_digest import build_digest
from bus_watch.navigator_wake import fire_navigator_wake
from bus_watch.tick_state import load_state, update_state


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--root", default=os.environ.get("LIAISON_ROOT", ""))
    p.add_argument(
        "--fire",
        action="store_true",
        help="SSH keystroke induction into the lock-holder tab",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print induction + remote command; no SSH",
    )
    p.add_argument(
        "--budget-tokens",
        type=int,
        default=int(os.environ.get("LIAISON_BUDGET_TOKENS", "700000")),
    )
    p.add_argument("--remote-repo", default=DEFAULT_REMOTE_REPO)
    p.add_argument(
        "--gui-host",
        default=None,
        help="override policy.gui_host for this fire only",
    )
    p.add_argument(
        "--no-raise",
        action="store_true",
        help="type into the window the operator has focused; skip compositor activate",
    )
    args = p.parse_args()
    root = str(args.root).strip()
    if not root:
        raise SystemExit("--root (or LIAISON_ROOT) required")
    if not args.fire and not args.dry_run:
        raise SystemExit("one of --fire or --dry-run is required")
    if args.fire and args.dry_run:
        raise SystemExit("--fire and --dry-run are mutually exclusive")

    state_path = WATCH_DIR / f"liaison-{root}.tick.json"
    state = load_state(state_path)
    register = str(state.get("register") or "attended")
    digest = build_digest(
        root, state, register=register, budget_tokens=args.budget_tokens
    )
    fp = digest.get("fingerprint")
    if fp and fp == state.get("last_induction_fingerprint"):
        out = {
            "ok": False,
            "phase": "already_fired",
            "root": root,
            "fingerprint": fp,
            "fix": "digest unchanged since last induction fire",
        }
        print(json.dumps(out, indent=2))
        return 2

    induction = str(digest.get("induction") or "")
    if not induction:
        out = {"ok": False, "phase": "no_induction", "root": root}
        print(json.dumps(out, indent=2))
        return 2

    transport = "ide" if register == "attended" else "navigator"
    if transport == "navigator":
        fired = fire_navigator_wake(
            root,
            digest,
            state,
            register=register,
            dry_run=args.dry_run,
        )
        evaluation = fired.get("evaluation") or {}
        out = {
            "ok": True if args.dry_run else bool(fired.get("ok")),
            "transport": transport,
            "root": root,
            "fingerprint": fp,
            "clauses": evaluation.get("clauses"),
            "skip_reason": fired.get("refused") or evaluation.get("skip_reason"),
            "doorbell_bytes": evaluation.get("doorbell_bytes"),
            "would_fire": fired.get("would_fire"),
        }
        if args.dry_run:
            out["doorbell"] = evaluation.get("doorbell") or fired.get("doorbell")
            out["body"] = fired.get("body")
        if fired.get("payload"):
            out["payload"] = fired["payload"]
        print(json.dumps(out, indent=2))
        if not out.get("ok"):
            return 2
        if args.fire and fp and fired.get("ok"):

            def _mark_induction(fresh: dict) -> None:
                fresh["last_induction_fingerprint"] = fp
                fresh["last_induction_at"] = time.time()
                if "navigator_commissions_by_night" in state:
                    fresh["navigator_commissions_by_night"] = state[
                        "navigator_commissions_by_night"
                    ]
                if "navigator_commissions_tonight" in state:
                    fresh["navigator_commissions_tonight"] = state[
                        "navigator_commissions_tonight"
                    ]

            update_state(state_path, _mark_induction)
        return 0

    out = fire_ide_followup(
        induction,
        root_id=root,
        gui_host=args.gui_host or policy_gui_host(root),
        remote_repo=args.remote_repo,
        dry_run=args.dry_run,
        no_raise=args.no_raise,
    )
    out["transport"] = transport
    out["fingerprint"] = fp
    out["induction"] = induction
    print(json.dumps(out, indent=2))
    if not out.get("ok"):
        return 2
    if args.fire and fp:
        # The GUI hop above can block for minutes; write against the file as it
        # is now, never the snapshot from before the wait (see update_state).
        def _mark_ide_induction(fresh: dict) -> None:
            fresh["last_induction_fingerprint"] = fp
            fresh["last_induction_at"] = time.time()

        update_state(state_path, _mark_ide_induction)
    return 0


if __name__ == "__main__":
    sys.exit(main())
