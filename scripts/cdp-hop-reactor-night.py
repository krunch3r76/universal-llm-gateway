#!/usr/bin/env python3
"""Mock CDP hop reactor — harvest Fable chat, respawn with continuity, bounded Composer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from bus_watch.state import write_state
from cdp_hop_reactor_night import (
    ReactorState,
    build_successor_prompt,
    format_harvest_turns,
    load_harvest_chain,
    load_state,
    parse_composer_signals,
    parse_next_leg,
    save_harvest,
    save_state,
)
from cdp_hop_reactor_wait import (
    active_row_absent_streak,
    build_harvest_request,
    harvest_miss_outcome,
    http_json_status,
    id_fields,
    is_cdp_external_gate_live,
    should_drop_satellite_id,
    terminal,
)
from cdp_hop_watch_steer import load_steer_hint, steer_path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
_STATE_DIR = Path(os.environ.get("CDP_HOP_STATE_DIR", str(_REPO / "tmp/watch/cdp-hop-reactor")))
_HARVEST_DIR = _STATE_DIR / "harvests"
_STATE_PATH = _STATE_DIR / "state.json"
_LOG_PATH = _STATE_DIR / "reactor.jsonl"
_CHARTER = Path(os.environ.get("CDP_HOP_CHARTER", str(_REPO / "tmp/prompts/cdp-hop-reactor-fable-charter.md")))

_STARGATE = os.environ.get("CDP_HOP_STARGATE", "http://localhost:9999").rstrip("/")
_CDP_ASK = os.environ.get("CDP_HOP_CDP_ASK", "http://jupiter:8770").rstrip("/")
_DISPATCH = f"{_STARGATE}/api/v1/team/dispatch"
_ACTIVE_WORK = f"{_CDP_ASK}/v1/project-ask/active-work"
_HARVEST_URL = f"{_CDP_ASK}/v1/cse-session/harvest"
_EXECUTION = f"{_STARGATE}/api/v1/pipelines/executions"

_EMAIL_BRIDGE_SOCK = os.environ.get("EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock")

POLL_S = int(os.environ.get("CDP_HOP_POLL_S", "60"))
IDLE_CONFIRMATIONS = int(os.environ.get("CDP_HOP_IDLE_CONFIRMATIONS", "2"))
MAX_FABLE_EPISODES = int(os.environ.get("CDP_HOP_MAX_FABLE_EPISODES", "12"))
MAX_COMPOSER_PER_TODO = int(os.environ.get("CDP_HOP_MAX_COMPOSER_PER_TODO", "2"))
HARVEST_CHAIN_DEPTH = int(os.environ.get("CDP_HOP_HARVEST_CHAIN_DEPTH", "3"))
ATTACH_GRACE_S = int(os.environ.get("CDP_HOP_ATTACH_GRACE_S", "180"))
ATTACH_ROW_ABSENT_POLLS = int(os.environ.get("CDP_HOP_ATTACH_ROW_ABSENT_POLLS", "4"))
COMPOSER_WAIT_SLICE_S = int(os.environ.get("CDP_HOP_COMPOSER_WAIT_SLICE_S", "120"))
COMPOSER_WAIT_MAX_S = int(os.environ.get("CDP_HOP_COMPOSER_WAIT_MAX_S", "7200"))
HARVEST_MISS_LIMIT = 5

TODO_THREADS = {"10160": "10160", "10094": "10094"}
TODO_SOURCE_REF = {"10160": "todo:ulg-orientation-flow", "10094": "todo:sketch-admit-contract-flatten"}


def log(event: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event}
    rec.update({k: v for k, v in fields.items() if v is not None})
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: float = 60.0) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url) if method == "GET" else client.post(url, json=payload)
    except httpx.HTTPError as exc:
        log("http_error", url=url, error=str(exc)[:300])
        return None
    env = http_json_status(resp.status_code, resp.text, parsed=_safe_json(resp))
    if resp.status_code >= 400:
        log("http_status", url=url, status=resp.status_code, code=env.get("code"))
        return env
    body = env.get("body")
    return body if isinstance(body, dict) else None


def _safe_json(resp: httpx.Response) -> dict[str, Any] | None:
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def page_stop(reason: str, **fields: Any) -> None:
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return
    detail = " ".join(f"{k}={v}" for k, v in sorted(fields.items()) if v)
    body = f"cdp-hop-reactor stopped: {reason}\n{detail}\nlog: {_LOG_PATH}"
    subprocess.run(
        ["curl", "-sS", "--unix-socket", _EMAIL_BRIDGE_SOCK, "-H", "Content-Type: application/json",
         "-d", json.dumps({"subject": f"CDP hop reactor stop — {reason}", "body": body, "tag": "cdp-hop-reactor"}),
         "http://localhost/pager/notify"],
        capture_output=True, check=False,
    )


def stop(reason: str, code: int = 0, **fields: Any) -> int:
    log("stop", reason=reason, **fields)
    page_stop(reason, **fields)
    return code


def active_work() -> dict[str, Any] | None:
    return http_json("GET", _ACTIVE_WORK, timeout=15.0)


def seated_satellite_id(data: dict[str, Any], fable_thread: str) -> str | None:
    for row in data.get("rows") or []:
        if str(row.get("parent_thread") or "") != fable_thread:
            continue
        exec_id = str(row.get("execution_id") or "").strip()
        if exec_id and not exec_id.startswith("__none"):
            return exec_id
    return None


def apply_harvest_ids(state: ReactorState, harvest: dict[str, Any]) -> None:
    if harvest.get("chat_url"):
        state.fable_chat_url = str(harvest["chat_url"])
    cursor = harvest.get("cursor")
    if cursor is not None:
        try:
            state.fable_last_turn_ordinal = int(cursor)
        except (TypeError, ValueError):
            pass
    prov = harvest.get("provenance") or {}
    if isinstance(prov, dict):
        if prov.get("registration_id"):
            state.fable_registration_id = str(prov["registration_id"])
        sat = prov.get("satellite_execution_id") or prov.get("execution_id")
        if sat:
            state.fable_satellite_execution_id = str(sat)


def harvest_cse(state: ReactorState, *, wait_ms: int = 5000) -> dict[str, Any] | None:
    req = build_harvest_request(
        chat_url=state.fable_chat_url,
        satellite_execution_id=state.fable_satellite_execution_id,
        stargate_execution_id=state.fable_stargate_execution_id,
    )
    req["limit"] = 50
    req["source"] = "auto"
    req["waited_ms"] = wait_ms
    result = http_json("POST", _HARVEST_URL, req, timeout=max(30.0, wait_ms / 1000 + 20))
    if isinstance(result, dict) and result.get("status", 200) >= 400:
        return {"outcome": "http_error", "envelope": result}
    return result if isinstance(result, dict) else None


def fire_fable(state: ReactorState, prompt: str) -> str | None:
    body = {"op": "generate", "model": "cdp/fable", "purpose": "mission", "contract": "light-bounded",
            "dispatch_thread_id": state.fable_thread, "prompt": prompt, "caller_agent": "cdp-hop-reactor-night"}
    resp = http_json("POST", _DISPATCH, body, timeout=120.0)
    if resp is None:
        log("fable_dispatch_failed", response=None)
        return None
    if resp.get("status", 200) >= 400:
        if is_cdp_external_gate_live(resp):
            log("fable_gate_409", **id_fields(state))
            h = harvest_cse(state)
            if h:
                apply_harvest_ids(state, h)
            return None
        log("fable_dispatch_failed", status=resp.get("status"), code=resp.get("code"))
        return None
    if resp.get("error"):
        log("fable_dispatch_failed", response=resp)
        return None
    exec_id = str(resp.get("execution_id") or "").strip() or None
    if exec_id:
        state.fable_stargate_execution_id = exec_id
    log("fable_dispatched", execution_id=exec_id, episode=state.fable_episode + 1)
    return exec_id


def await_fable_attach(state: ReactorState, exec_id: str) -> str | None:
    row_absent_streak = 0
    grace_start: float | None = None
    while True:
        time.sleep(15)
        data = active_work()
        if data is None:
            continue
        rows = data.get("rows") or []
        seated = seated_satellite_id(data, state.fable_thread)
        if seated:
            state.fable_satellite_execution_id = seated
            return seated
        if not active_row_absent_streak(rows, state.fable_thread):
            row_absent_streak = 0
            grace_start = None
            continue
        row_absent_streak += 1
        if grace_start is None:
            grace_start = time.time()
        if row_absent_streak >= ATTACH_ROW_ABSENT_POLLS and grace_start and time.time() - grace_start >= ATTACH_GRACE_S:
            log("fable_attach_timeout", execution_id=exec_id, row_absent_streak=row_absent_streak)
            if state.fable_chat_url:
                return None
            return exec_id


def poll_composer_terminal(execution_id: str) -> dict[str, Any]:
    deadline = time.time() + COMPOSER_WAIT_MAX_S
    last: dict[str, Any] = {}
    while time.time() < deadline:
        rec = http_json("GET", f"{_EXECUTION}/{execution_id}?wait={min(COMPOSER_WAIT_SLICE_S, 60)}",
                        timeout=COMPOSER_WAIT_SLICE_S + 15.0)
        if rec:
            last = rec
            if str(rec.get("status") or "") in {"completed", "failed", "cancelled"}:
                return rec
        time.sleep(POLL_S)
    return last


def fire_composer(todo_id: str, task: str) -> str | None:
    prompt = (f"# Bounded implement — todo:{todo_id}\n\n**Reactor-assigned task:** {task}\n\n"
              "Scope: complete only this slice. Lane B. Commit path-explicit when ACs met.")
    body = {"op": "generate", "seat": "cursor-sdk", "contract": "implement", "lane": "B",
            "dispatch_thread_id": TODO_THREADS[todo_id], "source_ref": TODO_SOURCE_REF.get(todo_id, f"todo:{todo_id}"),
            "prompt": prompt, "caller_agent": "cdp-hop-reactor-night"}
    resp = http_json("POST", _DISPATCH, body, timeout=120.0)
    if resp is None or resp.get("error"):
        return None
    return str(resp.get("execution_id") or "").strip() or None


def drain_composer_queue(state: ReactorState) -> list[str]:
    notes: list[str] = []
    remaining: list[dict[str, str]] = []
    for item in state.pending_composer:
        todo_id, task = item["todo_id"], item["task"]
        budget = state.composer[todo_id]
        if budget.count >= budget.max_per_todo:
            continue
        exec_id = fire_composer(todo_id, task)
        if not exec_id:
            remaining.append(item)
            continue
        record = poll_composer_terminal(exec_id)
        budget.count += 1
        budget.last_execution_id = exec_id
        notes.append(f"- todo:{todo_id} status={record.get('status')}")
    state.pending_composer = remaining
    return notes


def wait_fable_stream_end(state: ReactorState) -> tuple[str, None]:
    idle_streak = 0
    harvest_miss_streak = 0
    while True:
        data = active_work()
        running = False
        if data and state.fable_satellite_execution_id:
            for row in data.get("rows") or []:
                if str(row.get("parent_thread") or "") == state.fable_thread:
                    if str(row.get("status") or "") in {"pending", "running"}:
                        running = True
        harvest = harvest_cse(state)
        if not harvest:
            harvest_miss_streak += 1
            if harvest_miss_streak >= HARVEST_MISS_LIMIT:
                log("harvest_miss_breaker", **id_fields(state))
                return "harvest_unreachable", None
            time.sleep(POLL_S)
            continue
        harvest_miss_streak = 0 if not harvest_miss_outcome(str(harvest.get("outcome") or ""), turns=harvest.get("turns")) else harvest_miss_streak + 1
        apply_harvest_ids(state, harvest)
        if should_drop_satellite_id(harvest):
            log("satellite_id_dropped", outcome=str(harvest.get("outcome") or ""), **id_fields(state))
            state.fable_satellite_execution_id = None
        outcome = str(harvest.get("outcome") or "")
        streaming = bool(harvest.get("streaming"))
        tool_pause = bool(harvest.get("tool_pause"))
        stop_flag = bool(harvest.get("stop"))
        if outcome == "streaming" or running:
            idle_streak = 0
        else:
            idle_streak += 1
        if terminal(outcome, stop_flag, streaming, tool_pause, idle_streak, idle_confirmations=IDLE_CONFIRMATIONS):
            log("fable_stream_terminal", outcome=outcome, **id_fields(state))
            return "harvest_stop" if stop_flag else "idle", None
        if harvest_miss_streak >= HARVEST_MISS_LIMIT:
            return "harvest_unreachable", None
        time.sleep(POLL_S)


def run_cycle(state: ReactorState, *, deadline_epoch: float) -> str:
    if time.time() >= deadline_epoch or state.fable_episode >= MAX_FABLE_EPISODES:
        return "deadline" if time.time() >= deadline_epoch else "episode_budget"
    composer_notes = drain_composer_queue(state)
    prompt = build_successor_prompt(
        episode=state.fable_episode + 1, charter_path=_CHARTER,
        harvest_chain=load_harvest_chain(_HARVEST_DIR, HARVEST_CHAIN_DEPTH, state.last_harvest_seq),
        composer_notes=composer_notes, last_next_leg=state.last_next_leg,
        steer_hint=load_steer_hint(steer_path(_STATE_DIR)),
    )
    work = active_work() or {}
    seated = seated_satellite_id(work, state.fable_thread)
    if seated:
        state.fable_satellite_execution_id = seated
        log("fable_resume_seated", execution_id=seated)
    else:
        exec_id = fire_fable(state, prompt)
        if not exec_id:
            retry = active_work() or {}
            seated = seated_satellite_id(retry, state.fable_thread)
            if not seated:
                return "dispatch_fail"
            state.fable_satellite_execution_id = seated
        else:
            adopted = await_fable_attach(state, exec_id)
            if not adopted and not state.fable_chat_url:
                return "dispatch_fail"
    state.fable_episode += 1
    save_state(_STATE_PATH, state)
    reason, _ = wait_fable_stream_end(state)
    log("fable_stream_end", reason=reason, **id_fields(state))
    harvest = harvest_cse(state, wait_ms=8000)
    if not harvest:
        return "continue"
    apply_harvest_ids(state, harvest)
    turns = harvest.get("turns") or []
    outcome = str(harvest.get("outcome") or "unknown")
    if outcome == "harvested" and len(turns) >= 1:
        state.last_harvest_seq += 1
        save_harvest(_HARVEST_DIR, state.last_harvest_seq, execution_id=state.fable_satellite_execution_id,
                     outcome=outcome, body=format_harvest_turns(turns))
    state.last_next_leg = parse_next_leg(format_harvest_turns(turns))
    for todo_id, task in parse_composer_signals(format_harvest_turns(turns)):
        if state.composer[todo_id].count < state.composer[todo_id].max_per_todo:
            state.pending_composer.append({"todo_id": todo_id, "task": task})
    save_state(_STATE_PATH, state)
    log("episode_complete", episode=state.fable_episode, harvest_seq=state.last_harvest_seq)
    return "continue"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fable-thread", required=True)
    parser.add_argument("--deadline-hours", type=float, default=float(os.environ.get("CDP_HOP_DEADLINE_HOURS", "6")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-file", default="")
    args = parser.parse_args()
    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    state = load_state(_STATE_PATH)
    state.fable_thread = args.fable_thread.strip()
    for budget in state.composer.values():
        budget.max_per_todo = MAX_COMPOSER_PER_TODO
    deadline_epoch = time.time() + args.deadline_hours * 3600.0
    log("start", fable_thread=state.fable_thread, episode=state.fable_episode)
    if state_path:
        write_state(state_path, status="armed", fable_thread=state.fable_thread, episode=state.fable_episode, label="cdp-hop")
    while True:
        if state_path:
            write_state(state_path, status="running", fable_thread=state.fable_thread, episode=state.fable_episode)
        outcome = run_cycle(state, deadline_epoch=deadline_epoch)
        if outcome == "continue":
            if args.once:
                if state_path:
                    write_state(state_path, status="once_complete", episode=state.fable_episode)
                return 0
            continue
        if state_path:
            write_state(state_path, status="stopped", reason=outcome, episode=state.fable_episode)
        if outcome == "deadline":
            return stop("deadline", episodes=state.fable_episode)
        if outcome == "episode_budget":
            return stop("episode_budget", episodes=state.fable_episode)
        if outcome == "dispatch_fail":
            return stop("dispatch_fail", 1, episodes=state.fable_episode)
        return stop(outcome, episodes=state.fable_episode)


if __name__ == "__main__":
    sys.exit(main())
