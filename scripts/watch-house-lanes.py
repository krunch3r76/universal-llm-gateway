#!/usr/bin/env python3
"""Poll house child lanes + parent checkpoint; emit machine lines for relay.

Arm:
  scripts/watch-supervise.sh start --label house-10223-lanes --no-page -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-house-lanes.py \\
    --label house-10223-lanes --parent 10223
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import httpx
from bus_watch.state import paths_for, write_state
from house_bus_dispatch import _bus_client, bus_token, fetch_thread_turns

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_PARENT = "10223"
_POLL_S = 60.0
_DEFAULT_CHILDREN = "10303,10327"


def _thread_detail(client: httpx.Client, thread: str) -> dict[str, Any]:
    resp = client.get(f"http://localhost/threads/{thread}")
    resp.raise_for_status()
    return resp.json()


def _emit(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")
    print(line, flush=True)


def _scan_child(
    client: httpx.Client,
    thread: str,
    seen: dict[str, int],
    log_path: Path,
) -> None:
    detail = _thread_detail(client, thread)
    slug = str(detail.get("slug") or thread)
    turn_count = int(detail.get("turn_count") or 0)
    prev = seen.get(thread, 0)
    if turn_count <= prev:
        return
    turns = fetch_thread_turns(thread, last=min(5, turn_count - prev))
    for turn in reversed(turns):
        tn = int(turn.get("turn_number") or 0)
        if tn <= prev:
            continue
        subject = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        from_agent = str(turn.get("from") or "")
        if "CLOSEOUT" in subject.upper() or "cursor-sdk CLOSEOUT" in subject:
            status = "partial"
            if '"status":"complete"' in body or "verdict: LANDED" in body:
                status = "complete"
            elif "checks_failed" in body:
                status = "checks_failed"
            _emit(
                log_path,
                f"lane_closeout thread={thread} slug={slug} turn={tn} "
                f"status={status} from={from_agent} subject={subject[:80]}",
            )
        elif subject.startswith("CHECKPOINT"):
            _emit(
                log_path,
                f"checkpoint_child thread={thread} turn={tn} subject={subject[:80]}",
            )
    seen[thread] = turn_count


def _scan_parent_checkpoint(
    client: httpx.Client,
    parent: str,
    seen_cp: int,
    log_path: Path,
) -> int:
    turns = fetch_thread_turns(parent, last=1)
    if not turns:
        return seen_cp
    turn = turns[0]
    subject = str(turn.get("subject") or "")
    tn = int(turn.get("turn_number") or 0)
    if subject.startswith("CHECKPOINT") and tn > seen_cp:
        _emit(
            log_path,
            f"checkpoint_parent thread={parent} turn={tn} subject={subject[:120]}",
        )
        return tn
    return seen_cp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="house-10223-lanes")
    parser.add_argument("--parent", default=_DEFAULT_PARENT)
    parser.add_argument("--children", default=_DEFAULT_CHILDREN)
    parser.add_argument("--poll-s", type=float, default=_POLL_S)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch-dir", default=str(_REPO / "tmp" / "watchers"))
    parser.add_argument("--state-file", default="")
    args = parser.parse_args()

    label = str(args.label).strip()
    parent = str(args.parent).strip()
    children = [x.strip() for x in str(args.children).split(",") if x.strip()]
    paths = paths_for(label, directory=Path(args.watch_dir))
    log_path = paths.log_file
    state_file = (
        Path(args.state_file) if str(args.state_file).strip() else paths.state_file
    )
    seen_child: dict[str, int] = {}
    seen_cp = 0
    token = bus_token()

    write_state(
        state_file,
        status="armed",
        label=label,
        parent=parent,
        children=children,
    )
    print(
        f"watch-house-lanes label={label} parent={parent} children={children}",
        flush=True,
    )

    while True:
        with _bus_client(token, timeout_s=20.0) as client:
            seen_cp = _scan_parent_checkpoint(client, parent, seen_cp, log_path)
            for child in children:
                try:
                    _scan_child(client, child, seen_child, log_path)
                except httpx.HTTPError as exc:
                    _emit(log_path, f"scan_error thread={child} error={exc}")

        write_state(
            state_file,
            status="polling",
            label=label,
            parent=parent,
            seen_child=seen_child,
            seen_checkpoint_turn=seen_cp,
        )
        if args.once:
            write_state(state_file, status="once_complete", label=label)
            return 0
        time.sleep(max(15.0, float(args.poll_s)))


if __name__ == "__main__":
    raise SystemExit(main())
