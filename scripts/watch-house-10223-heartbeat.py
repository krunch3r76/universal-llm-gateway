#!/usr/bin/env python3
"""House 10223 overnight heartbeat — relay 10303 closeouts + signal WORK subagent launch.

Polls the closeout lane (default agent-bus:10303), posts INFO relays on the
coordinator thread (default 10223), and when the tab-launch index has a **ready**
row with no in-flight work, emits an IDE wake sentinel so the orchestrator tab
can launch a Task subagent against the prompt (context stays in-session).

Usage:
  # Single tick (dry-run relay + state update):
  scripts/watch-house-10223-heartbeat.py --once

  # Emit Cursor loop wake when action needed:
  scripts/watch-house-10223-heartbeat.py --once --emit-wake

  # Detached poller (optional; IDE loop is the primary overnight path):
  scripts/watch-supervise.sh start --label house-10223-hb -- \\
    scripts/watch-house-10223-heartbeat.py --loop --interval-seconds 600 --emit-wake

State: tmp/watchers/house-10223-heartbeat.state.json
Wake:  AGENT_LOOP_WAKE_HOUSE10223 {"prompt":"..."}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml

from bus_watch.state import read_state, write_state

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_INDEX_PATH = _REPO / "tmp/prompts/tab-launch-index-10223.md"
_WAKE_SENTINEL = "AGENT_LOOP_WAKE_HOUSE10223"
_LABEL = "house-10223-heartbeat"
_DEFAULT_COORDINATOR = "10223"
_DEFAULT_CLOSEOUT_LANE = "10303"
_DEFAULT_INTERVAL_S = 600


def _token() -> str:
    with open(_MCP_YAML) as f:
        cfg = yaml.safe_load(f)
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus_client(token: str) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=30.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _fetch_turns(client: httpx.Client, thread_id: str, *, last: int = 30) -> list[dict[str, Any]]:
    resp = client.get(
        f"http://localhost/turns?{urlencode({'thread': thread_id, 'last': last, 'compact': 'false'})}"
    )
    resp.raise_for_status()
    rows = resp.json().get("turns") or []
    return rows if isinstance(rows, list) else []


def _is_closeout(row: dict[str, Any]) -> bool:
    subject = str(row.get("subject") or "").upper()
    return "CLOSEOUT" in subject


def _relay_info(
    client: httpx.Client,
    *,
    coordinator: str,
    closeout_lane: str,
    turn: dict[str, Any],
) -> dict[str, Any]:
    tn = turn.get("turn_number")
    subject = str(turn.get("subject") or "CLOSEOUT")
    body = str(turn.get("body") or "").strip()
    preview = body[:1200] if body else "(empty body — fetch full turn if needed)"
    payload = {
        "thread": coordinator,
        "to": "cursor",
        "from": "cursor",
        "subject": f"INFO relay {closeout_lane}#{tn} — {subject[:80]}",
        "body": (
            f"Heartbeat relay from closeout lane **{closeout_lane}#{tn}**.\n\n"
            f"**Subject:** {subject}\n\n"
            f"**Preview:**\n{preview}\n"
        ),
    }
    resp = client.post("http://localhost/threads/send", json=payload)
    resp.raise_for_status()
    data = resp.json()
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    tn = turn.get("turn_number")
    if tn is None and isinstance(data.get("thread"), dict):
        tn = data["thread"].get("turn_count")
    return {"turn_number": tn, "raw": data}


def _parse_ready_rows(index_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in index_text.splitlines():
        if "|" not in line or "**ready**" not in line.lower():
            continue
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) < 5:
            continue
        prompt_cell = parts[1].strip("` ")
        if not prompt_cell.endswith(".md"):
            continue
        rows.append(
            {
                "prompt": prompt_cell,
                "priority": parts[3].strip(),
                "notes": parts[4].strip(),
            }
        )
    return rows


def _tick(
    *,
    coordinator: str,
    closeout_lane: str,
    state_path: Path,
    dry_run: bool,
) -> dict[str, Any]:
    token = _token()
    state = read_state(state_path)
    last_seen = int(state.get("last_closeout_turn") or 0)
    relayed: set[int] = set(state.get("relayed_closeout_turns") or [])
    in_flight = state.get("in_flight_prompt")
    bootstrapped = bool(state.get("bootstrapped"))

    result: dict[str, Any] = {
        "status": "polling",
        "new_closeouts": [],
        "relayed": [],
        "work_pending": None,
        "ready_rows": [],
        "in_flight_prompt": in_flight,
        "bootstrapped": bootstrapped,
    }

    with _bus_client(token) as client:
        turns = _fetch_turns(client, closeout_lane, last=40)
        turns_sorted = sorted(turns, key=lambda r: int(r.get("turn_number") or 0))
        max_turn = last_seen
        for row in turns_sorted:
            tn = int(row.get("turn_number") or 0)
            max_turn = max(max_turn, tn)
            if tn <= last_seen:
                continue
            if not _is_closeout(row):
                continue
            result["new_closeouts"].append({"turn": tn, "subject": row.get("subject")})
            if not bootstrapped:
                # First arm: advance watermark only — do not replay lane history.
                continue
            if tn in relayed:
                continue
            if dry_run:
                result["relayed"].append({"turn": tn, "dry_run": True})
            else:
                created = _relay_info(
                    client,
                    coordinator=coordinator,
                    closeout_lane=closeout_lane,
                    turn=row,
                )
                result["relayed"].append(
                    {
                        "turn": tn,
                        "relay_turn": created.get("turn_number"),
                    }
                )
                relayed.add(tn)

    if not bootstrapped:
        bootstrapped = True

    if _INDEX_PATH.is_file():
        ready = _parse_ready_rows(_INDEX_PATH.read_text(encoding="utf-8"))
        result["ready_rows"] = ready
        if ready and not in_flight:
            top = ready[0]
            result["work_pending"] = {
                "prompt_path": str(_REPO / "tmp/prompts" / top["prompt"]),
                "prompt_file": top["prompt"],
                "priority": top["priority"],
                "notes": top["notes"],
            }

    write_state(
        state_path,
        status="polling",
        label=_LABEL,
        coordinator_thread=coordinator,
        closeout_lane=closeout_lane,
        last_closeout_turn=max_turn,
        relayed_closeout_turns=sorted(relayed),
        in_flight_prompt=in_flight,
        bootstrapped=bootstrapped,
        last_tick=result,
    )
    return result


def _wake_prompt(result: dict[str, Any]) -> str | None:
    parts: list[str] = [
        "House 10223 heartbeat tick. Execute bound legs only.",
        "Read tmp/watchers/house-10223-heartbeat.state.json for last_tick.",
    ]
    if result.get("relayed"):
        parts.append(
            f"Closeouts relayed: {json.dumps(result['relayed'])} — summarize in chat if material."
        )
    work = result.get("work_pending")
    if work:
        parts.append(
            "WORK_PENDING: launch ONE Task(subagent_type=generalPurpose, run_in_background=true) "
            f"with the full contents of {work['prompt_path']}. "
            "Inject task-subagent-parity kernel (reasoning-posture, provenance, CLOSEOUT 10303). "
            "Before launch: python scripts/watch-house-10223-heartbeat.py --mark-in-flight "
            f"{work['prompt_file']!r}. "
            "Subagent posts CLOSEOUT on agent-bus:10303 only."
        )
    if not result.get("relayed") and not work:
        return None
    parts.append("Do not hold the turn on long waits. Next heartbeat in ~10m.")
    return " ".join(parts)


def _mark_in_flight(state_path: Path, prompt_file: str | None) -> None:
    write_state(
        state_path,
        in_flight_prompt=prompt_file,
        in_flight_marked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coordinator", default=_DEFAULT_COORDINATOR)
    parser.add_argument("--closeout-lane", default=_DEFAULT_CLOSEOUT_LANE)
    parser.add_argument("--state-file", default=str(_REPO / "tmp/watchers/house-10223-heartbeat.state.json"))
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument("--loop", action="store_true", help="Poll forever (use with watch-supervise)")
    parser.add_argument("--interval-seconds", type=int, default=_DEFAULT_INTERVAL_S)
    parser.add_argument("--emit-wake", action="store_true", help="Print AGENT_LOOP_WAKE sentinel when action needed")
    parser.add_argument("--dry-run", action="store_true", help="Do not post bus relays")
    parser.add_argument("--mark-in-flight", metavar="PROMPT_FILE", help="Record in-flight WORK prompt filename")
    parser.add_argument("--clear-in-flight", action="store_true", help="Clear in_flight_prompt after CLOSEOUT")
    args = parser.parse_args()

    state_path = Path(args.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if args.mark_in_flight:
        _mark_in_flight(state_path, args.mark_in_flight)
        print(json.dumps({"marked_in_flight": args.mark_in_flight}))
        return 0
    if args.clear_in_flight:
        _mark_in_flight(state_path, None)
        print(json.dumps({"cleared_in_flight": True}))
        return 0

    def run_once() -> dict[str, Any]:
        tick = _tick(
            coordinator=args.coordinator,
            closeout_lane=args.closeout_lane,
            state_path=state_path,
            dry_run=args.dry_run,
        )
        print(json.dumps(tick, indent=2))
        if args.emit_wake:
            prompt = _wake_prompt(tick)
            if prompt:
                payload = json.dumps({"prompt": prompt})
                print(f"{_WAKE_SENTINEL} {payload}", flush=True)
        if tick.get("new_closeouts") and not args.dry_run:
            write_state(state_path, in_flight_prompt=None)
        return tick

    if args.loop:
        while True:
            run_once()
            time.sleep(max(30, args.interval_seconds))
    run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
