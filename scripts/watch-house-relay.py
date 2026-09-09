#!/usr/bin/env python3
"""Tail house watcher logs and dispatch cursor-auto investigate on 10223.

No cursor-sdk dispatch — operator bind (house-orchestration-steer).

Arm:
  scripts/watch-supervise.sh start --label house-10223-relay --no-page -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-house-relay.py \\
    --label house-10223-relay --thread 10223
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import httpx
from bus_watch.state import paths_for, write_state
from house_bus_dispatch import (
    bus_reply,
    dispatch_cursor_eval,
    fetch_after_turn,
    request_id_for_trigger,
)

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_THREAD = "10223"
_DEBOUNCE_S = 300.0
_POLL_S = 2.0
_AFTER_TURN_REFRESH_S = 120.0

_TRIGGER_PREFIXES = (
    "lane_closeout ",
    "checkpoint_parent ",
    "checkpoint_child ",
    "closeout turn=",
)
_CLOSEOUT_PREFIX = "closeout turn="

_EVAL_BODY = """TYPE: WATCHER_RELAY
contract: investigate
thread: {thread}

**Trigger:** `{trigger_line}`

**Question:** House orchestration event — relay verdict, update journal, recommend next WORK tab?

**Steer:** `tmp/prompts/house-orchestration-steer-2026-09-08.md`
- RELAY_ONLY default · NO team_dispatch cursor-sdk · WORK_TAB_ONLY

**Read (bounded):**
- agent-bus:{thread} latest CHECKPOINT (not linear thread read)
- Trigger thread if `lane_closeout` names one
- `cortex://notes/system/threads/10223-opportunities.md`
- Append one line to `tmp/orchestration/journal.jsonl` (create dir if needed)

**Deliver:** INFO-style UPDATE — verdict, next WORK tab packet path if implement needed, BLOCKER if any.
**Forbidden:** commits · manage sync_restart · team_dispatch generate
"""


def _is_material(line: str) -> bool:
    if line.startswith(_CLOSEOUT_PREFIX):
        return True
    return any(line.startswith(p) for p in _TRIGGER_PREFIXES)


def _trigger_key(line: str) -> str:
    if line.startswith(_CLOSEOUT_PREFIX):
        return "closeout"
    for prefix in _TRIGGER_PREFIXES:
        if line.startswith(prefix):
            return prefix.split()[0]
    return "other"


def _tail_paths(labels: list[str], watch_dir: Path) -> list[Path]:
    return [paths_for(label, directory=watch_dir).log_file for label in labels]


def _emit_dispatch(
    *,
    thread: str,
    trigger_line: str,
    after_turn: int,
    label: str,
    debounce: dict[str, float],
    debounce_s: float,
    now: float,
) -> dict[str, Any] | None:
    key = _trigger_key(trigger_line)
    last = debounce.get(key, 0.0)
    if now - last < debounce_s:
        print(f"relay_debounce skip key={key} age={now - last:.0f}s", flush=True)
        return None
    debounce[key] = now

    subject = f"WATCHER {key} — house relay"
    body = _EVAL_BODY.format(thread=thread, trigger_line=trigger_line.strip())
    req_id = request_id_for_trigger(key, trigger_line)

    print(f"relay_dispatch trigger={trigger_line[:120]} request_id={req_id}", flush=True)
    try:
        auto_result = dispatch_cursor_eval(
            thread=thread,
            subject=subject,
            body=body,
            after_turn=after_turn,
            contract="investigate",
            request_id=req_id,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            print(f"relay_skip_409 trigger={trigger_line[:80]}", flush=True)
            return None
        raise

    turn_obj = auto_result.get("turn") or {}
    turn_n = turn_obj.get("turn_number")
    handler = auto_result.get("handler_status")
    print(f"relay_complete turn={turn_n} handler_status={handler}", flush=True)

    update_body = "\n".join(
        [
            f"**UPDATE** (watcher `{label}`)",
            "",
            f"Trigger: `{trigger_line.strip()}`",
            f"cursor-auto: turn={turn_n} handler={handler} request_id={req_id}",
        ]
    )
    try:
        bus_reply(
            thread=thread,
            subject=f"UPDATE — {key}",
            body=update_body,
            after_turn=int(turn_n or after_turn),
        )
    except Exception as exc:
        print(f"relay_update_failed: {exc}", flush=True)

    return auto_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="house-10223-relay")
    parser.add_argument("--thread", default=_DEFAULT_THREAD)
    parser.add_argument(
        "--watch-labels",
        default="house-10223-lanes,house-10223-closeout",
    )
    parser.add_argument("--debounce-s", type=float, default=_DEBOUNCE_S)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch-dir", default=str(_REPO / "tmp" / "watchers"))
    parser.add_argument("--state-file", default="")
    args = parser.parse_args()

    debounce_s = max(30.0, float(args.debounce_s))
    label = str(args.label).strip()
    thread = str(args.thread).strip()
    watch_dir = Path(args.watch_dir)
    watch_labels = [x.strip() for x in str(args.watch_labels).split(",") if x.strip()]
    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    log_paths = _tail_paths(watch_labels, watch_dir)
    offsets: dict[str, int] = {}
    debounce: dict[str, float] = {}
    after_turn = 0
    after_turn_at = 0.0

    print(
        f"watch-house-relay label={label} thread={thread} "
        f"watch={','.join(watch_labels)} debounce={debounce_s:g}s",
        flush=True,
    )
    for lp in log_paths:
        key = str(lp)
        if lp.is_file():
            offsets[key] = 0 if args.once else lp.stat().st_size
        else:
            offsets[key] = 0

    if state_path is not None:
        write_state(state_path, status="armed", label=label, thread=thread)

    while True:
        now = time.time()
        if after_turn == 0 or now - after_turn_at > _AFTER_TURN_REFRESH_S:
            try:
                after_turn = fetch_after_turn(thread)
                after_turn_at = now
            except Exception as exc:
                print(f"after_turn_refresh_failed: {exc}", flush=True)

        saw_line = False
        for lp in log_paths:
            if not lp.is_file():
                continue
            text = lp.read_text(encoding="utf-8", errors="replace")
            start = offsets.get(str(lp), 0)
            if start > len(text):
                start = len(text)
            chunk = text[start:]
            offsets[str(lp)] = len(text)
            for raw in chunk.splitlines():
                line = raw.strip()
                if not line or not _is_material(line):
                    continue
                saw_line = True
                _emit_dispatch(
                    thread=thread,
                    trigger_line=line,
                    after_turn=after_turn,
                    label=label,
                    debounce=debounce,
                    debounce_s=debounce_s,
                    now=now,
                )

        if state_path is not None:
            write_state(
                state_path,
                status="polling",
                label=label,
                thread=thread,
                after_turn=after_turn,
            )

        if args.once:
            if state_path is not None:
                write_state(state_path, status="once_complete", label=label)
            return 0

        if not saw_line:
            print(f"… heartbeat label={label} sleep={_POLL_S:g}s", flush=True)
        time.sleep(_POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
