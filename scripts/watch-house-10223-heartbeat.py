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
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml
from bus_watch.state import read_state, write_state
from orchestrator_handoff.queue import HandoffQueue
from orchestrator_handoff.repair import repair_tick
from orchestrator_handoff.work_prompt import summarize_work_prompt

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_INDEX_PATH = _REPO / "tmp/prompts/tab-launch-index-10223.md"
_OPPORTUNITIES_PATH = _REPO / "cortex" / "notes/system/threads/10223-opportunities.md"
# Fallback when cortex mount not in repo — try fs path via repo-relative notes if synced
if not _OPPORTUNITIES_PATH.is_file():
    _OPPORTUNITIES_PATH = _REPO / "tmp/prompts/10223-opportunities.md"
if not _OPPORTUNITIES_PATH.is_file():
    _OPPORTUNITIES_PATH = (
        Path(os.environ.get("CORTEX_FILES_ROOT", str(Path.home() / "mcp-data/files")))
        / "notes/system/threads/10223-opportunities.md"
    )
_WAKE_SENTINEL = "AGENT_LOOP_WAKE_HOUSE10223"
_LABEL = "house-10223-heartbeat"
_DEFAULT_COORDINATOR = "10223"
_DEFAULT_CLOSEOUT_LANE = "10303"
_DEFAULT_INTERVAL_S = 600
_STALL_TICKS = 2
_HANDOFF_SCRIPT = _REPO / "scripts/orchestrator-tab-handoff.py"
_LOCK_PATH = _REPO / "tmp/watchers/orchestrator-handoff.lock"


def _parse_utc_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _orchestrator_lock_held() -> bool:
    """True when repair left a live handoff lock on disk (same source as repair_tick)."""
    return _LOCK_PATH.is_file()


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


def _fetch_turns(
    client: httpx.Client, thread_id: str, *, last: int = 30
) -> list[dict[str, Any]]:
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


def _parse_opportunity_ready(opportunities_text: str) -> list[dict[str, str]]:
    """Rows with Status `ready` and a tab-launch or tmp/prompts follow-on path."""
    rows: list[dict[str, str]] = []
    current_title = ""
    for line in opportunities_text.splitlines():
        if line.startswith("### "):
            current_title = line.removeprefix("### ").strip()
        if "**Status**" in line and "`ready`" in line.lower():
            rows.append({"opportunity": current_title, "status": "ready"})
        if "tab-launch" in line and ".md" in line:
            m = re.search(r"`([^`]+\.md)`", line)
            if m and rows and rows[-1].get("opportunity") == current_title:
                rows[-1]["prompt"] = m.group(1).split("/")[-1]
    return [r for r in rows if r.get("prompt")]


def _tick(
    *,
    coordinator: str,
    closeout_lane: str,
    state_path: Path,
    dry_run: bool,
    interval_seconds: int = _DEFAULT_INTERVAL_S,
) -> dict[str, Any]:
    token = _token()
    state = read_state(state_path)
    last_seen = int(state.get("last_closeout_turn") or 0)
    relayed: set[int] = set(state.get("relayed_closeout_turns") or [])
    bootstrapped = bool(state.get("bootstrapped"))

    result: dict[str, Any] = {
        "status": "polling",
        "new_closeouts": [],
        "relayed": [],
        "work_pending": None,
        "bootstrapped": bootstrapped,
        "repairs": [],
    }

    queue = HandoffQueue.open()
    result["repairs"] = repair_tick(queue, lock_path=_LOCK_PATH, index_path=_INDEX_PATH)

    try:
        token = _token()
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
                result["new_closeouts"].append(
                    {"turn": tn, "subject": row.get("subject")}
                )
                if not bootstrapped:
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
    except Exception as exc:  # noqa: BLE001 — heartbeat must survive bus wedge
        result["bus_error"] = str(exc)[:500]
        max_turn = last_seen

    if not bootstrapped:
        bootstrapped = True

    lock_held = _orchestrator_lock_held()
    result["orchestrator_lock_held"] = lock_held

    queue_peek = queue.peek()
    if not queue_peek and not lock_held:
        disc = queue.discover_work(
            index_path=_INDEX_PATH,
            opportunities_path=_OPPORTUNITIES_PATH
            if _OPPORTUNITIES_PATH.is_file()
            else None,
            enqueue_opportunities=True,
        )
        result["discover"] = disc
        queue_peek = queue.peek()
        if disc.get("opportunities"):
            result["opportunity_candidates"] = disc["opportunities"]
        if disc.get("consult_suggested") and not queue_peek:
            result["consult_suggested"] = True
    result["queue_peek"] = queue_peek
    result["queue_active"] = queue.active_item()

    if queue_peek and not lock_held:
        wp = queue_peek.get("work_prompt") or ""
        prompt_path = _REPO / wp if wp else None
        if prompt_path and not prompt_path.is_file():
            prompt_path = None
        meta = summarize_work_prompt(prompt_path or wp) if wp else {}
        result["work_pending"] = {
            "source": "registrar_queue",
            "queue_id": queue_peek.get("id"),
            "intent": queue_peek.get("intent"),
            "prompt_path": str(prompt_path) if prompt_path else wp,
            "prompt_file": Path(wp).name if wp else "",
            "priority": queue_peek.get("priority", ""),
            "notes": queue_peek.get("notes", ""),
            "work_class": meta.get(
                "work_class", queue_peek.get("work_class", "mechanical")
            ),
        }

    productive = bool(
        result.get("relayed")
        or result.get("work_pending")
        or result.get("repairs")
        or result.get("new_closeouts")
    )
    last_productive_at = state.get("last_productive_at")
    if productive:
        last_productive_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    skipped_ready = list(
        (result.get("discover") or {}).get("import_ready", {}).get("skipped") or []
    )
    wedge_signals = {
        "orchestrator_lock_held": lock_held,
        "queue_active": bool(result.get("queue_active")),
        "ready_skipped": skipped_ready,
        "queue_empty": not queue_peek and not result.get("queue_active"),
    }
    hopper_status = "polling"
    if wedge_signals["orchestrator_lock_held"] or wedge_signals["queue_active"]:
        hopper_status = "degraded"
    elif wedge_signals["ready_skipped"] and wedge_signals["queue_empty"]:
        hopper_status = "degraded"
    elif last_productive_at:
        parsed = _parse_utc_ts(str(last_productive_at))
        if parsed is not None:
            age_s = (datetime.now(UTC) - parsed).total_seconds()
            if age_s > interval_seconds * _STALL_TICKS:
                hopper_status = "stalled"
    result["hopper_status"] = hopper_status
    result["wedge_signals"] = wedge_signals

    write_state(
        state_path,
        status=hopper_status,
        label=_LABEL,
        coordinator_thread=coordinator,
        closeout_lane=closeout_lane,
        last_closeout_turn=max_turn,
        relayed_closeout_turns=sorted(relayed),
        bootstrapped=bootstrapped,
        last_tick=result,
        last_productive_at=last_productive_at,
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
    if result.get("orchestrator_lock_held"):
        parts.append(
            "ORCHESTRATOR_LOCK: tmp/watchers/orchestrator-handoff.lock held — "
            "skip WORK launch until release (see tmp/prompts/orchestrator-tab-handoff-protocol.md)."
        )
    work = result.get("work_pending")
    if work:
        wc = work.get("work_class", "mechanical")
        if wc == "swarm":
            parts.append(
                "WORK_PENDING (swarm): keystroke launch opens a hop tab — "
                f"Multitask ON; session lead executes {work['prompt_path']} and spawns "
                "Task(Grok) W* slices per prompt. CLOSEOUT 10303 + CHECKPOINT 10223 required."
            )
        elif wc == "audit":
            parts.append(
                "WORK_PENDING (audit): launch hop tab for evaluate-only "
                f"{work['prompt_path']}. Multitask OFF; no Task. CLOSEOUT 10303 when done."
            )
        elif wc == "implement":
            parts.append(
                "WORK_PENDING (implement): launch hop tab — Multitask OFF; "
                f"execute {work['prompt_path']} in-seat. Sequential Task only "
                "(composer-2.5 / grok-4.6-xhigh, one at a time). CLOSEOUT 10303 when done."
            )
        else:
            parts.append(
                "WORK_PENDING (mechanical): launch hop tab for in-seat implement "
                f"{work['prompt_path']}. CLOSEOUT 10303 when done."
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
    parser.add_argument(
        "--state-file",
        default=str(_REPO / "tmp/watchers/house-10223-heartbeat.state.json"),
    )
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument(
        "--loop", action="store_true", help="Poll forever (use with watch-supervise)"
    )
    parser.add_argument("--interval-seconds", type=int, default=_DEFAULT_INTERVAL_S)
    parser.add_argument(
        "--emit-wake",
        action="store_true",
        help="Print AGENT_LOOP_WAKE sentinel when action needed",
    )
    parser.add_argument(
        "--emit-keystroke-launch",
        action="store_true",
        help="When WORK pending, run orchestrator-tab-handoff.py launch on orion-node (Wayland)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not post bus relays")
    parser.add_argument(
        "--mark-in-flight",
        metavar="PROMPT_FILE",
        help="Record in-flight WORK prompt filename",
    )
    parser.add_argument(
        "--clear-in-flight",
        action="store_true",
        help="Clear in_flight_prompt after CLOSEOUT",
    )
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
            interval_seconds=max(30, args.interval_seconds),
        )
        print(json.dumps(tick, indent=2))
        if args.emit_keystroke_launch:
            work = tick.get("work_pending")
            if work and not tick.get("orchestrator_lock_held"):
                queue = HandoffQueue.open()
                queue_id = work.get("queue_id")
                if queue_id:
                    queue.mark_launching(str(queue_id))
                wp = work.get("prompt_path") or ""
                intent = str(work.get("intent") or Path(wp).stem or "heartbeat-work")[
                    :80
                ]
                launch_cmd = [
                    sys.executable,
                    str(_REPO / "scripts/orchestrator-tab-handoff.py"),
                    "launch",
                    "--intent",
                    intent,
                    "--work-prompt",
                    wp,
                ]
                if queue_id:
                    launch_cmd.extend(["--queue-id", str(queue_id)])
                proc = subprocess.run(
                    launch_cmd, capture_output=True, text=True, timeout=120
                )
                launch_meta: dict[str, Any] = {
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-2000:],
                    "stderr": proc.stderr[-500:],
                }
                if proc.returncode == 0 and queue_id:
                    try:
                        out = json.loads(proc.stdout.strip().split("\n")[-1])
                        holder = out.get("holder")
                        if holder:
                            queue.mark_in_flight(str(queue_id), str(holder))
                            launch_meta["holder"] = holder
                    except json.JSONDecodeError:
                        pass
                elif queue_id and proc.returncode != 0:
                    queue.requeue_launch_failure(
                        str(queue_id),
                        proc.stderr[:200] or "launch_failed",
                    )
                print(json.dumps({"keystroke_launch": launch_meta}), flush=True)
        elif args.emit_wake:
            prompt = _wake_prompt(tick)
            if prompt:
                payload = json.dumps({"prompt": prompt})
                print(f"{_WAKE_SENTINEL} {payload}", flush=True)
        return tick

    if args.loop:
        while True:
            run_once()
            time.sleep(max(30, args.interval_seconds))
    run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
