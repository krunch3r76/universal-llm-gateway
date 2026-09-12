#!/usr/bin/env python3
"""Liaison tick — one lean, read-only digest per wake for a continuity-root seat.

The liaison (attended or autonomous register) must not read the bus linearly:
each wake it consumes ONE digest line and decides (harvest · fold · dispatch ·
checkpoint · hop · park). This script produces that line.

Modes:
  --once                 print the digest JSON and exit (dogfood / manual tick)
  --loop                 emit ``AGENT_LOOP_TICK_liaison <json>`` when a watched
                         lane changed or the heartbeat elapsed; the IDE tab arms
                         it as a monitored background shell (``/loop`` local
                         mechanism) so the sentinel wakes the seat.
  --loop --spawn-on-wake gear-3 ticker: poll bus, spawn successor on attention

Digest contents: root + child lanes (lineage), per-lane turn/unread counters,
terminal-class last subjects, unread TOC scoped to those lanes, completed
watcher state files not yet relayed, fleet health, and a context-budget
governor (ticks · estimated tokens · stop class). Budget is an *estimate*
carried with its basis — the seat treats ``stop_class`` as a designed stop,
never as proof of exhaustion.

Bus access mirrors scripts/watch-cursor-bridge-inbox.py (UDS + AGENT_BUS_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import httpx
from bus_watch.digest_publish import publish_if_enabled
from bus_watch.fable_lock import (
    WATCH_DIR as _WATCH_DIR,
)
from bus_watch.fable_lock import (
    claim_fable_lock,
    claim_ticker_lease,
    read_lock,
    refresh_fable_lock,
    refresh_ticker_lease,
    release_fable_lock,
    release_ticker_lease,
)
from bus_watch.liaison_digest import (
    TICK_OVERHEAD_TOKENS as _TICK_OVERHEAD_TOKENS,
)
from bus_watch.liaison_digest import (
    build_digest,
    effective_policy,
)
from bus_watch.spawn_on_wake import tick_spawn_on_wake
from bus_watch.tick_state import absorb_operator_edits, load_state, save_state

_SENTINEL = "AGENT_LOOP_TICK_liaison"


def _coerce(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _utcnow() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--root",
        default=os.environ.get("LIAISON_ROOT", ""),
        help="continuity root thread id",
    )
    p.add_argument("--register", choices=["attended", "autonomous"], default=None)
    p.add_argument(
        "--budget-tokens",
        type=int,
        default=int(os.environ.get("LIAISON_BUDGET_TOKENS", "700000")),
    )
    p.add_argument("--state-file", default="")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument(
        "--spawn-on-wake",
        action="store_true",
        help="gear-3: spawn successor on attention/checkpoint_due (with --loop)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate spawn predicate without firing (with --spawn-on-wake)",
    )
    p.add_argument(
        "--poll", type=int, default=60, help="seconds between bus polls in --loop"
    )
    p.add_argument(
        "--heartbeat",
        type=int,
        default=1800,
        help="max seconds between sentinels in --loop",
    )
    p.add_argument(
        "--mark-checkpoint",
        action="store_true",
        help="record that the seat just checkpointed",
    )
    p.add_argument(
        "--mark-relayed",
        default="",
        help="comma-separated watcher state file names now relayed",
    )
    p.add_argument(
        "--holder",
        default="",
        help="liaison seat (ide:<transcript_id>|sdk:<dispatch_id>); default ide:<root> co-holds across tabs",
    )
    p.add_argument(
        "--claim",
        action="store_true",
        help="claim the single-Fable lock for --holder (no digest)",
    )
    p.add_argument(
        "--hop", action="store_true", help="with --claim: count this claim as a hop"
    )
    p.add_argument(
        "--release",
        action="store_true",
        help="release the single-Fable lock held by --holder",
    )
    p.add_argument(
        "--take-over",
        action="store_true",
        help="with --claim/--loop: preempt live ide: holder (operator resume <root> on another host)",
    )
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set a policy knob (gear, successor_model, max_ticks_per_hop, …); repeatable",
    )
    p.add_argument(
        "--policy", action="store_true", help="print the effective policy and exit"
    )
    args = p.parse_args()

    if args.claim or args.release:
        if not args.holder:
            raise SystemExit("--holder required with --claim/--release")
        policy = effective_policy({"policy": {}})
        result = (
            claim_fable_lock(
                args.holder,
                hop=args.hop,
                max_hop_minutes=float(policy.get("max_hop_minutes") or 60),
                root_id=str(args.root or "").strip(),
                take_over=args.take_over,
            )
            if args.claim
            else release_fable_lock(args.holder)
        )
        print(json.dumps(result))
        return 0 if result.get("ok") else 3

    root = str(args.root).strip()
    if not root:
        raise SystemExit("--root (or LIAISON_ROOT) required")
    state_path = (
        Path(args.state_file)
        if args.state_file
        else _WATCH_DIR / f"liaison-{root}.tick.json"
    )
    state = load_state(state_path)
    register = args.register or str(state.get("register") or "attended")
    state["register"] = register
    state.setdefault("born_epoch", time.time())
    state.setdefault("born_at", _utcnow())

    if args.mark_checkpoint:
        state["last_cp_tick"] = int(state.get("ticks") or 0)
    if args.mark_relayed:
        relayed = set(state.get("relayed_watchers") or [])
        relayed.update(x.strip() for x in args.mark_relayed.split(",") if x.strip())
        state["relayed_watchers"] = sorted(relayed)
    if args.set:
        policy = dict(state.get("policy") or {})
        for item in args.set:
            key, sep, raw = item.partition("=")
            if not sep or not key.strip():
                raise SystemExit(f"--set expects KEY=VALUE, got {item!r}")
            policy[key.strip()] = _coerce(raw.strip())
        state["policy"] = policy
    if args.mark_checkpoint or args.mark_relayed or args.set or args.policy:
        save_state(state_path, state)
        if not (args.once or args.loop):
            print(
                json.dumps(
                    {
                        "ok": True,
                        "state": str(state_path),
                        "policy": effective_policy(state),
                    }
                )
            )
            return 0

    if not args.loop:
        digest = build_digest(
            root, state, register=register, budget_tokens=args.budget_tokens
        )
        if args.holder and read_lock().get("holder") == args.holder:
            policy = digest.get("policy") or {}
            refresh_fable_lock(
                args.holder,
                max_hop_minutes=float(policy.get("max_hop_minutes") or 60),
                turns_seen=int((digest.get("root") or {}).get("turns") or 0),
            )
        save_state(state_path, state)
        print(json.dumps(digest, default=str))
        return 0

    if args.spawn_on_wake:
        return _spawn_loop(args, root, state, state_path, register)

    holder = args.holder or f"ide:{root}"
    claim = claim_fable_lock(holder, hop=False, root_id=root, take_over=args.take_over)
    if not claim.get("ok"):
        print(
            json.dumps(
                {
                    "loop": "refused",
                    "reason": "fable_lock_held",
                    "lock": claim.get("lock"),
                }
            ),
            flush=True,
        )
        return 3
    last_emit = 0.0
    print(
        json.dumps(
            {
                "loop": "armed",
                "root": root,
                "holder": holder,
                "poll_s": args.poll,
                "heartbeat_s": args.heartbeat,
            }
        ),
        flush=True,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return _loop(args, root, state, state_path, register, holder, last_emit)
    finally:
        release_fable_lock(holder)


def _spawn_loop(args, root, state, state_path, register):  # noqa: ANN001, ANN202
    claim = claim_ticker_lease(root)
    if not claim.get("ok"):
        print(
            json.dumps(
                {"loop": "refused", "reason": "ticker_held", "lock": claim.get("lock")}
            ),
            flush=True,
        )
        return 3
    policy = effective_policy(state)
    poll_s = int(policy.get("poll_seconds") or args.poll)
    print(
        json.dumps(
            {
                "loop": "spawn_on_wake",
                "root": root,
                "poll_s": poll_s,
                "dry_run": bool(args.dry_run),
                "policy_ready": policy.get("ready"),
            }
        ),
        flush=True,
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            if not refresh_ticker_lease(root):
                print(json.dumps({"loop": "ticker_lost", "root": root}), flush=True)
                return 4
            _log_steer(absorb_operator_edits(state, state_path))
            try:
                digest = build_digest(
                    root, state, register=register, budget_tokens=args.budget_tokens
                )
            except (httpx.HTTPError, OSError) as exc:
                print(
                    json.dumps({"loop": "transport_error", "error": str(exc)[:200]}),
                    flush=True,
                )
                time.sleep(poll_s)
                continue
            spawn_result = tick_spawn_on_wake(digest, state, root, dry_run=args.dry_run)
            publish_if_enabled(root, digest, state, require_change=True)
            save_state(state_path, state)
            line = {
                "spawn": spawn_result,
                "digest_ts": digest.get("ts"),
                "attention": digest.get("attention"),
                "checkpoint_due": digest.get("checkpoint_due"),
            }
            print(json.dumps(line, default=str), flush=True)
            if args.dry_run:
                return 0
            time.sleep(poll_s)
    finally:
        release_ticker_lease(root)


def _log_steer(changed: list[str]) -> None:
    if changed:
        print(
            json.dumps({"loop": "operator_edit_absorbed", "keys": changed}), flush=True
        )


def _loop(args, root, state, state_path, register, holder, last_emit):  # noqa: ANN001, ANN202, PLR0913
    while True:
        if not refresh_fable_lock(holder) or read_lock().get("preempt_by"):
            print(
                json.dumps(
                    {"loop": "preempted", "holder": holder, "lock": read_lock()}
                ),
                flush=True,
            )
            return 4
        _log_steer(absorb_operator_edits(state, state_path))
        try:
            digest = build_digest(
                root, state, register=register, budget_tokens=args.budget_tokens
            )
        except (httpx.HTTPError, OSError) as exc:
            print(
                json.dumps({"loop": "transport_error", "error": str(exc)[:200]}),
                flush=True,
            )
            time.sleep(args.poll)
            continue
        now = time.monotonic()
        due = (
            digest["changed_since_last_tick"]
            or (now - last_emit) >= args.heartbeat
            or (digest.get("budget") or {}).get("stop_class")
        )
        if due:
            save_state(state_path, state)
            publish_if_enabled(root, digest, state) and save_state(state_path, state)
            print(f"{_SENTINEL} {json.dumps(digest, default=str)}", flush=True)
            last_emit = now
        else:
            state["ticks"] = int(state["ticks"]) - 1
            state["est_tokens"] = (
                int(state["est_tokens"])
                - _TICK_OVERHEAD_TOKENS
                - len(json.dumps(digest)) // 4
            )
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
