#!/usr/bin/env python3
"""Poll a cursor-sdk dispatch until closeout; page operator once.

Watches agent-bus thread for the cursor-sdk closeout turn
(completion=first_reply_from, from_agent=cursor-sdk). Optionally cross-checks
frontier.sdk.worker.completed via Event Service.

Prefer detached arm via scripts/watch-supervise.sh so Cursor Shell abort cannot
kill the poller. In-window wake = supervise tail of the log (default
exit-on-complete when state.json status=complete; --forever debug-only).

Usage:
  scripts/watch-supervise.sh start --label '74874-close' -- \\
    scripts/watch-dispatch-closeout.py --thread 6361 --dispatch-id 74874a907d5d-9beddd66
  scripts/watch-supervise.sh tail --label '74874-close'   # exits on complete; notify on closeout turn=
  scripts/watch-dispatch-closeout-tmux.sh --latest --label 'my arc'
    scripts/watch-dispatch-closeout.py --thread 6361 --dispatch-id 74874a907d5d-9beddd66
  scripts/watch-dispatch-closeout.py --thread 9916 --execution-id 78b387c6-73e7-43f5-bd34-f05f413d3b45
  # both from a team_dispatch admit is OK — --dispatch-id wins
  scripts/watch-dispatch-closeout.py --latest
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml
from bus_watch.arm_contract import NO_PRODUCER_HELP, require_producer_declaration
from bus_watch.poll import (
    DEFAULT_MAX_HOURS,
    DEFAULT_WAIT_SLICE_S,
    EXIT_PRODUCER_TERMINAL,
)
from bus_watch.stall_pop import closeout_terminal_stall_reason, emit_stall_pop
from bus_watch.state import write_state

_REPO = Path(__file__).resolve().parents[1]
_QUERY_EVENTS = _REPO / "scripts" / "query-events"
_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_WAIT_SECONDS = 55.0  # client timeout floor; loop uses --wait-slice-seconds
_POLL_SLEEP_S = 2.0


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
        timeout=_WAIT_SECONDS + 10.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _query_events(sql: str, *params: str) -> list[dict[str, Any]]:
    if not _QUERY_EVENTS.is_file():
        return []
    cmd = [str(_QUERY_EVENTS), "--sql", sql, "--limit", "5", "--compact"]
    for param in params:
        cmd.extend(["--sql-param", param])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    payload = json.loads(proc.stdout or "{}")
    rows = payload.get("rows")
    return rows if isinstance(rows, list) else []


def _resolve_latest_dispatch() -> tuple[str, str, str]:
    rows = _query_events(
        "SELECT payload FROM events "
        "WHERE signal = 'frontier.sdk.worker.dispatched' "
        "ORDER BY seq DESC LIMIT 1"
    )
    if not rows:
        raise SystemExit("no frontier.sdk.worker.dispatched event found (--latest)")
    data = json.loads(str(rows[0].get("payload") or "{}"))
    thread_id = str(data.get("thread_id") or "").strip()
    dispatch_id = str(data.get("dispatch_id") or "").strip()
    execution_id = str(data.get("execution_id") or "").strip()
    if not thread_id or not dispatch_id:
        raise SystemExit(f"malformed dispatched event: {data!r}")
    return thread_id, dispatch_id, execution_id


def _resolve_execution_id(*, watch_id: str, explicit: str) -> str:
    """Pin producer projection on agent_bus wait (matches watch-bus-consult-and-page)."""
    if explicit.strip():
        return explicit.strip()
    payload = _latest_event_payload(watch_id, "frontier.sdk.worker.dispatched")
    if payload:
        return str(payload.get("execution_id") or "").strip()
    return ""


def _wait_closeout(
    client: httpx.Client,
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str,
    wait_s: int,
    execution_id: str = "",
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": wait_s,
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    if execution_id:
        params["execution_id"] = execution_id
    resp = client.get(f"http://localhost/threads/{thread_id}/wait?{urlencode(params)}")
    resp.raise_for_status()
    return resp.json()


def _fetch_closeout_turn(
    client: httpx.Client,
    thread_id: str,
    turn: int,
) -> dict[str, Any]:
    resp = client.get(
        f"http://localhost/turns?{urlencode({'thread': thread_id, 'last': 5, 'compact': 'false'})}"
    )
    resp.raise_for_status()
    for row in resp.json().get("turns") or []:
        if row.get("turn_number") == turn:
            return row
    return {}


def _parse_closeout_json(body: str) -> dict[str, Any]:
    text = (body or "").strip()
    if not text.startswith("{") or "schema_version" not in text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_sdk_closeout_turn(row: dict[str, Any]) -> bool:
    """True only for machine CLOSEOUT — not CHECKPOINT / PARK / other cursor-sdk turns."""
    subject = str(row.get("subject") or "")
    if "cursor-sdk CLOSEOUT" in subject:
        return True
    return bool(_parse_closeout_json(str(row.get("body") or "")).get("schema_version"))


def _closeout_thread_id(closeout: dict[str, Any], *, fallback: str = "") -> str:
    effects = closeout.get("effects_manifest")
    if isinstance(effects, dict):
        tid = str(effects.get("thread_id") or "").strip()
        if tid:
            return tid
    evidence = closeout.get("evidence_uris")
    if isinstance(evidence, dict):
        buses = evidence.get("bus_threads") or []
        if buses:
            return str(buses[0]).strip()
    return str(fallback or "").strip()


def _failed_verification_commands(closeout: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for row in closeout.get("verification") or []:
        if not isinstance(row, dict):
            continue
        exit_code = row.get("exit_code")
        if exit_code is None:
            continue
        if int(exit_code) != 0:
            failed.append(str(row.get("command") or "unknown"))
    return failed


def _arc_outcome_line(
    closeout: dict[str, Any],
    *,
    label: str,
    thread_id: str = "",
) -> tuple[str, str]:
    """Return (subject, body) for operator pager from this closeout payload only."""
    summary = str(closeout.get("summary") or "").strip()
    work_outcome = str(closeout.get("work_outcome") or "").strip()
    status = str(closeout.get("status") or "").strip()
    outcome_token = work_outcome or status or "closeout"
    bus_thread = _closeout_thread_id(closeout, fallback=thread_id)

    subject_bits = [label]
    if bus_thread:
        subject_bits.append(f"thread {bus_thread}")
    subject_bits.append(outcome_token)
    subject = " · ".join(subject_bits)[:120]

    body_parts: list[str] = []
    if summary:
        body_parts.append(summary[:500])
    work_key = str(closeout.get("work_key") or "").strip()
    if not work_key:
        work_key = str(closeout.get("source_ref") or "").strip()
    if work_key:
        body_parts.append(f"work_key: {work_key}")
    failed = _failed_verification_commands(closeout)
    if failed:
        body_parts.append("checks_failed: " + "; ".join(failed))
    if bus_thread:
        body_parts.append(f"thread: {bus_thread}")
    return subject, "\n\n".join(body_parts)


def _generic_dispatch_page(
    *,
    label: str,
    thread_id: str,
    outcome: str,
    elapsed_s: float,
    duration: float | int | None,
    model: str,
    worker_subject: str,
    parent_harvest_subject: str | None = None,
) -> tuple[str, str]:
    if parent_harvest_subject:
        page_subject = parent_harvest_subject[:120]
    else:
        page_subject = f"ULG dispatch done — {label}"
    page_body = (
        f"thread {thread_id} · {outcome}"
        f" · elapsed {elapsed_s:.0f}s"
        f"{f' · worker {duration:.0f}s' if isinstance(duration, (int, float)) else ''}"
        f"{f' · {model}' if model else ''}"
        f"{f' · {worker_subject}' if worker_subject else ''}"
    )
    return page_subject, page_body


def _closeout_page_text(
    *,
    closeout_json: dict[str, Any],
    parent_subj: str | None,
    label: str,
    thread_id: str,
    outcome: str,
    elapsed_s: float,
    duration: float | int | None,
    model: str,
    worker_subject: str,
) -> tuple[str, str]:
    """Choose pager copy from closeout JSON, parent harvest, or generic fallback."""
    if closeout_json and str(closeout_json.get("summary") or "").strip():
        return _arc_outcome_line(closeout_json, label=label, thread_id=thread_id)
    if parent_subj and not closeout_json:
        return _generic_dispatch_page(
            label=label,
            thread_id=thread_id,
            outcome=outcome,
            elapsed_s=elapsed_s,
            duration=duration,
            model=model,
            worker_subject=worker_subject,
            parent_harvest_subject=parent_subj,
        )
    if closeout_json and not str(closeout_json.get("summary") or "").strip():
        return _generic_dispatch_page(
            label=label,
            thread_id=thread_id,
            outcome=outcome,
            elapsed_s=elapsed_s,
            duration=duration,
            model=model,
            worker_subject=worker_subject,
        )
    return _generic_dispatch_page(
        label=label,
        thread_id=thread_id,
        outcome=outcome,
        elapsed_s=elapsed_s,
        duration=duration,
        model=model,
        worker_subject=worker_subject,
    )


def _parent_harvest_subject(
    client: httpx.Client,
    parent_thread: str,
    *,
    after_turn: int,
) -> str | None:
    if not parent_thread:
        return None
    resp = client.get(
        f"http://localhost/turns?{urlencode({'thread': parent_thread, 'last': 5, 'compact': 'true'})}"
    )
    resp.raise_for_status()
    for row in resp.json().get("turns") or []:
        if int(row.get("turn_number") or 0) <= after_turn:
            continue
        subj = str(row.get("subject") or "")
        if row.get("from") == "cursor-sdk" and subj:
            return subj
    return None


def _watch_id_prefix(watch_id: str) -> str:
    return watch_id.split("-", 1)[0]


def _latest_event_payload(watch_id: str, signal: str) -> dict[str, Any] | None:
    prefix = _watch_id_prefix(watch_id)
    rows = _query_events(
        "SELECT payload FROM events "
        "WHERE signal = ? "
        "AND (json_extract(payload, '$.dispatch_id') LIKE ? "
        "OR json_extract(payload, '$.request_id') LIKE ? "
        "OR json_extract(payload, '$.execution_id') LIKE ?) "
        "ORDER BY seq DESC LIMIT 1",
        signal,
        f"{prefix}%",
        f"{prefix}%",
        f"{prefix}%",
    )
    if not rows:
        return None
    return json.loads(str(rows[0].get("payload") or "{}"))


def _worker_completed(watch_id: str) -> dict[str, Any] | None:
    return _latest_event_payload(watch_id, "frontier.sdk.worker.completed")


def _queue_status_line(watch_id: str, thread_id: str) -> str:
    """Human-readable queue / run phase from Event Service (non-terminal)."""
    completed = _worker_completed(watch_id)
    if completed:
        outcome = str(completed.get("outcome") or "unknown")
        duration = completed.get("duration_s")
        dur = f" · {duration:.0f}s" if isinstance(duration, (int, float)) else ""
        return f"phase=completed outcome={outcome}{dur}"

    promoted = _latest_event_payload(watch_id, "frontier.sdk.worker.lease.promoted")
    if promoted:
        progress = _latest_event_payload(watch_id, "frontier.sdk.worker.progress")
        if progress:
            elapsed = progress.get("elapsed_s")
            tools = progress.get("tool_call_count")
            el = f" · {elapsed:.0f}s" if isinstance(elapsed, (int, float)) else ""
            tc = f" · {tools} tools" if isinstance(tools, int) else ""
            return f"phase=running{el}{tc}"
        return "phase=running (lease promoted)"

    queued = _latest_event_payload(watch_id, "frontier.sdk.worker.queued")
    if queued:
        pos = queued.get("queue_position")
        holder = queued.get("holder_dispatch_id") or queued.get("holder_thread_id")
        model = queued.get("holder_resolved_model") or queued.get("resolved_model")
        preview = str(queued.get("holder_subject_preview") or "")[:48]
        parts = [f"phase=queued q={pos}" if pos is not None else "phase=queued"]
        if holder:
            parts.append(f"holder={holder}")
        if model:
            parts.append(f"holder_model={model}")
        if preview:
            parts.append(f"blocked_by={preview!r}")
        return " · ".join(parts)

    return f"phase=unknown (thread {thread_id} — no queue events yet)"


def _page_operator(*, subject: str, body: str, tag: str) -> bool:
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        print("pager disabled (PAGER_NOTIFY_ENABLED=0)", flush=True)
        return False
    payload = {
        "subject": subject[:120],
        "body": body[:4000],
        "tag": tag[:40],
    }
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            _EMAIL_BRIDGE_SOCK,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            "http://localhost/pager/notify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"pager curl failed: {proc.stderr.strip()}", flush=True)
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        print(f"pager bad response: {proc.stdout!r}", flush=True)
        return False
    ok = str(data.get("status")) == "sent"
    print(f"pager: {data}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", help="agent-bus worker thread id")
    parser.add_argument(
        "--dispatch-id",
        help="team_dispatch dispatch_id (preferred for event correlation)",
    )
    parser.add_argument(
        "--execution-id",
        help=(
            "team_dispatch execution_id (alias when --dispatch-id absent; "
            "ignored when both are set — prefer --dispatch-id)"
        ),
    )
    parser.add_argument(
        "--after-turn",
        type=int,
        default=1,
        help="pointer turn (default: admission turn 1)",
    )
    parser.add_argument(
        "--from-agent",
        default="cursor-sdk",
        help="wait for first reply from this agent (default: cursor-sdk; use cdp for CDP)",
    )
    parser.add_argument(
        "--label",
        default="cursor-sdk dispatch",
        help="short arc label for pager subject",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="resolve thread+dispatch from newest worker.dispatched event",
    )
    parser.add_argument(
        "--parent-thread",
        help="arc thread id — harvest subject from latest cursor-sdk turn after worker closeout",
    )
    parser.add_argument(
        "--parent-after-turn",
        type=int,
        default=0,
        help="only consider parent turns after this number (default: 0)",
    )
    parser.add_argument(
        "--come-to-ide",
        action="store_true",
        help="pager subject COME TO IDE — {label}; body reminds operator to return",
    )
    parser.add_argument(
        "--wait-slice-seconds",
        type=float,
        default=DEFAULT_WAIT_SLICE_S,
        help=f"long-poll slice + heartbeat cadence (default {DEFAULT_WAIT_SLICE_S:g})",
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=DEFAULT_MAX_HOURS,
        help=f"orphan ceiling (default {DEFAULT_MAX_HOURS:g})",
    )
    parser.add_argument(
        "--state-file",
        default="",
        help="durable JSON status path (watch-supervise sets this)",
    )
    parser.add_argument(
        "--no-page",
        action="store_true",
        help="print closeout only; do not SMS",
    )
    parser.add_argument(
        "--no-producer",
        action="store_true",
        help=NO_PRODUCER_HELP,
    )
    args = parser.parse_args()

    thread_id = str(args.thread or "").strip()
    dispatch_id = str(args.dispatch_id or "").strip()
    execution_id = str(args.execution_id or "").strip()
    # Admit payloads return both ids; XOR-exit was a footgun (15266 / 10534).
    if dispatch_id and execution_id:
        print(
            "note: both --dispatch-id and --execution-id set; preferring --dispatch-id",
            flush=True,
        )
    watch_id = dispatch_id or execution_id
    if args.latest or not thread_id or not watch_id:
        latest_thread, latest_dispatch, latest_execution = _resolve_latest_dispatch()
        thread_id = thread_id or latest_thread
        watch_id = watch_id or latest_dispatch
        execution_id = execution_id or latest_execution

    wait_execution_id = _resolve_execution_id(watch_id=watch_id, explicit=execution_id)
    arm_error = require_producer_declaration(
        wait_execution_id, no_producer=bool(args.no_producer)
    )
    if arm_error:
        emit_stall_pop(arm_error)
        print(f"watch-dispatch-closeout: {arm_error}", file=sys.stderr, flush=True)
        return 2

    token = _token()
    from_agent = str(args.from_agent or "cursor-sdk").strip()
    slice_s = max(1, int(float(args.wait_slice_seconds)))
    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    started_at = time.monotonic()
    print(
        f"watching thread={thread_id} watch_id={watch_id} "
        f"after_turn={args.after_turn} from_agent={from_agent} label={args.label!r} "
        f"wait_slice_s={slice_s} "
        f"{f'execution_id={wait_execution_id!r} ' if wait_execution_id else ''}"
        f"started_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        flush=True,
    )
    if state_path is not None:
        write_state(
            state_path,
            status="armed",
            thread=thread_id,
            after_turn=args.after_turn,
            label=args.label,
            execution_id=wait_execution_id,
            dispatch_id=dispatch_id,
        )

    after_turn = int(args.after_turn)
    unlinked_warned = False
    with _bus_client(token) as client:
        while True:
            elapsed_s = time.monotonic() - started_at
            if (
                float(args.max_hours) > 0
                and elapsed_s >= float(args.max_hours) * 3600.0
            ):
                print(
                    f"watcher expired label={args.label!r} elapsed={elapsed_s:.0f}s",
                    flush=True,
                )
                if state_path is not None:
                    write_state(
                        state_path, status="expired", elapsed_s=round(elapsed_s, 1)
                    )
                return 2
            print(
                f"… heartbeat label={args.label} thread={thread_id} "
                f"after_turn={after_turn} elapsed={elapsed_s:.0f}s",
                flush=True,
            )
            if from_agent == "cursor-sdk" and not _worker_completed(watch_id):
                print(_queue_status_line(watch_id, thread_id), flush=True)
            elif from_agent != "cursor-sdk":
                print(
                    f"phase=waiting for {from_agent} reply on thread {thread_id}",
                    flush=True,
                )
            snap = _wait_closeout(
                client,
                thread_id=thread_id,
                after_turn=after_turn,
                from_agent=from_agent,
                wait_s=slice_s,
                execution_id=wait_execution_id,
            )
            if wait_execution_id and not unlinked_warned:
                producer = snap.get("producer") or {}
                if producer.get("state") == "unlinked":
                    reason = (
                        f"mis-arm — execution_id={wait_execution_id!r} has no dispatch link on "
                        f"thread {thread_id}; producer death is undetectable from this watch"
                    )
                    emit_stall_pop(reason)
                    print(
                        f"watch-dispatch-closeout: {reason}",
                        file=sys.stderr,
                        flush=True,
                    )
                    unlinked_warned = True
            if snap.get("complete"):
                reply_turn = int(snap.get("qualifying_reply_turn") or 0)
                closeout_row = _fetch_closeout_turn(client, thread_id, reply_turn)
                if from_agent == "cursor-sdk" and not _is_sdk_closeout_turn(
                    closeout_row
                ):
                    subject = str(closeout_row.get("subject") or "")
                    print(
                        f"… non-closeout turn={reply_turn} subject={subject!r}; "
                        f"continuing after_turn={reply_turn}",
                        flush=True,
                    )
                    after_turn = reply_turn
                    if state_path is not None:
                        write_state(
                            state_path,
                            status="polling",
                            after_turn=after_turn,
                            last_status="skipped_non_closeout",
                        )
                    continue
                subject = str(closeout_row.get("subject") or "")
                closeout_json = _parse_closeout_json(
                    str(closeout_row.get("body") or "")
                )
                parent_subj = _parent_harvest_subject(
                    client,
                    str(args.parent_thread or "").strip(),
                    after_turn=args.parent_after_turn,
                )
                completed = _worker_completed(watch_id) or {}
                outcome = str(completed.get("outcome") or "unknown")
                if from_agent != "cursor-sdk":
                    outcome = f"{from_agent}-reply"
                duration = completed.get("duration_s")
                elapsed_s = time.monotonic() - started_at
                model = str(completed.get("resolved_model") or "")
                print(
                    f"closeout turn={reply_turn} subject={subject!r} "
                    f"outcome={outcome} duration_s={duration} elapsed_s={elapsed_s:.0f} model={model}",
                    flush=True,
                )
                if state_path is not None:
                    write_state(
                        state_path,
                        status="complete",
                        qualifying_reply_turn=reply_turn,
                        subject=subject,
                        outcome=outcome,
                    )
                if args.no_page:
                    return 0
                if args.come_to_ide:
                    page_subject = f"COME TO IDE — {args.label}"
                    page_body = (
                        f"thread {thread_id} closeout · {outcome}"
                        f" · friction 529 bind — return to Cursor IDE"
                        f" · elapsed {elapsed_s:.0f}s"
                        f"{f' · {subject}' if subject else ''}"
                    )
                else:
                    page_subject, page_body = _closeout_page_text(
                        closeout_json=closeout_json,
                        parent_subj=parent_subj,
                        label=args.label,
                        thread_id=thread_id,
                        outcome=outcome,
                        elapsed_s=elapsed_s,
                        duration=duration,
                        model=model,
                        worker_subject=subject,
                    )
                _page_operator(
                    subject=page_subject,
                    body=page_body,
                    tag="dispatch-done",
                )
                return 0

            terminal_reason = closeout_terminal_stall_reason(snap)
            if terminal_reason:
                emit_stall_pop(terminal_reason)
                print(
                    f"watcher terminal label={args.label!r} thread={thread_id} "
                    f"after_turn={after_turn} reason={terminal_reason}",
                    flush=True,
                )
                if state_path is not None:
                    write_state(
                        state_path,
                        status="terminal",
                        last_status=snap.get("status"),
                        terminal_reason=terminal_reason,
                        producer=snap.get("producer"),
                    )
                return EXIT_PRODUCER_TERMINAL

            turn_count = snap.get("turn_count")
            thread_status = snap.get("thread_status")
            print(
                f"… waiting ({thread_status}, turns={turn_count})",
                flush=True,
            )
            if state_path is not None:
                write_state(
                    state_path,
                    status="polling",
                    last_status=snap.get("status"),
                    turn_count=turn_count,
                    thread_status=thread_status,
                )
            time.sleep(_POLL_SLEEP_S)


if __name__ == "__main__":
    raise SystemExit(main())
