#!/usr/bin/env python3
"""Poll a cursor-sdk dispatch until closeout; page operator once.

Watches agent-bus thread for the cursor-sdk closeout turn
(completion=first_reply_from, from_agent=cursor-sdk). Optionally cross-checks
frontier.sdk.worker.completed via Event Service.

Usage:
  scripts/watch-dispatch-closeout-tmux.sh --latest --label 'my arc'
  scripts/watch-dispatch-closeout.py --thread 6361 --dispatch-id 74874a907d5d-9beddd66
  scripts/watch-dispatch-closeout.py --thread 9916 --execution-id 78b387c6-73e7-43f5-bd34-f05f413d3b45
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

_REPO = Path(__file__).resolve().parents[1]
_QUERY_EVENTS = _REPO / "scripts" / "query-events"
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_WAIT_SECONDS = 55.0
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


def _resolve_latest_dispatch() -> tuple[str, str]:
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
    if not thread_id or not dispatch_id:
        raise SystemExit(f"malformed dispatched event: {data!r}")
    return thread_id, dispatch_id


def _wait_closeout(
    client: httpx.Client,
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str,
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": int(_WAIT_SECONDS),
        "completion": "first_reply_from",
        "from_agent": from_agent,
    }
    resp = client.get(f"http://localhost/threads/{thread_id}/wait?{urlencode(params)}")
    resp.raise_for_status()
    return resp.json()


def _fetch_closeout_turn(
    client: httpx.Client, thread_id: str, turn: int,
) -> dict[str, Any]:
    resp = client.get(
        f"http://localhost/turns?{urlencode({'thread': thread_id, 'last': 5, 'compact': 'false'})}"
    )
    resp.raise_for_status()
    for row in resp.json().get("turns") or []:
        if row.get("turn_number") == turn:
            return row
    return {}


def _fetch_closeout_subject(client: httpx.Client, thread_id: str, turn: int) -> str:
    row = _fetch_closeout_turn(client, thread_id, turn)
    return str(row.get("subject") or "")


def _parse_closeout_json(body: str) -> dict[str, Any]:
    text = (body or "").strip()
    if not text.startswith("{") or "schema_version" not in text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _arc_outcome_line(
    closeout: dict[str, Any],
    *,
    label: str,
    parent_harvest_subject: str | None,
) -> tuple[str, str]:
    """Return (subject, body) for operator pager — outcome-first NL."""
    summary = str(closeout.get("summary") or "").strip()
    work_outcome = str(closeout.get("work_outcome") or "").strip()
    offgit = closeout.get("files_offgit_produced") or []
    spec_uri = ""
    for item in offgit:
        s = str(item)
        if "g3-spec" in s.lower() or s.endswith("g3-spec.md"):
            spec_uri = s
            break
    if not spec_uri and offgit:
        spec_uri = str(offgit[0])

    spec_landed = bool(spec_uri and "g3-spec" in spec_uri.lower())
    checks_failed = work_outcome == "checks_failed"

    if parent_harvest_subject:
        lead = parent_harvest_subject
    elif spec_landed and checks_failed:
        lead = f"{label}: implementation spec revised and landed (capture partial)"
    elif spec_landed:
        lead = f"{label}: implementation spec landed"
    elif work_outcome == "complete":
        lead = f"{label}: dispatch complete"
    elif checks_failed:
        lead = f"{label}: dispatch finished with capture gaps"
    else:
        lead = f"{label}: dispatch closeout"

    subject = lead[:120]
    vision = (
        "ULG event-store durability: embedded SQLite DBs must survive off NFS "
        "with retention and visible write-fail signals for trading audit."
    )
    architecture = (
        "Cortex spec + agent-bus scoreboard on the event-db corruption recovery "
        "arc — G3 locks files_expected before G4 skeptic and G5 implement."
    )
    body_parts = [vision, architecture]
    if spec_uri:
        body_parts.append(f"Spec: {spec_uri}")
    if summary:
        body_parts.append(summary[:500])
    if checks_failed:
        body_parts.append(
            "Worker closeout flagged capture partial (typical: off-git Cortex deliverables). "
            "Verify spec sha on thread 9938 before G4."
        )
    body_parts.append(
        "Next: G4 Opus skeptic re-run on rev 3 spec. Thread 9938 for detail."
    )
    return subject, "\n\n".join(body_parts)


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
        help="team_dispatch execution_id (alias; matches frontier.sdk.worker.* events)",
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
    args = parser.parse_args()

    thread_id = str(args.thread or "").strip()
    dispatch_id = str(args.dispatch_id or "").strip()
    execution_id = str(args.execution_id or "").strip()
    if dispatch_id and execution_id:
        raise SystemExit("pass only one of --dispatch-id or --execution-id")
    watch_id = dispatch_id or execution_id
    if args.latest or not thread_id or not watch_id:
        latest_thread, latest_dispatch = _resolve_latest_dispatch()
        thread_id = thread_id or latest_thread
        watch_id = watch_id or latest_dispatch

    token = _token()
    from_agent = str(args.from_agent or "cursor-sdk").strip()
    started_at = time.monotonic()
    print(
        f"watching thread={thread_id} watch_id={watch_id} "
        f"after_turn={args.after_turn} from_agent={from_agent} label={args.label!r} "
        f"started_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        flush=True,
    )

    with _bus_client(token) as client:
        while True:
            if from_agent == "cursor-sdk" and not _worker_completed(watch_id):
                print(_queue_status_line(watch_id, thread_id), flush=True)
            elif from_agent != "cursor-sdk":
                print(f"phase=waiting for {from_agent} reply on thread {thread_id}", flush=True)
            snap = _wait_closeout(
                client,
                thread_id=thread_id,
                after_turn=args.after_turn,
                from_agent=from_agent,
            )
            if snap.get("complete"):
                reply_turn = int(snap.get("qualifying_reply_turn") or 0)
                closeout_row = _fetch_closeout_turn(client, thread_id, reply_turn)
                subject = str(closeout_row.get("subject") or "")
                closeout_json = _parse_closeout_json(str(closeout_row.get("body") or ""))
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
                if not args.no_page:
                    if args.come_to_ide:
                        page_subject = f"COME TO IDE — {args.label}"
                        page_body = (
                            f"thread {thread_id} closeout · {outcome}"
                            f" · friction 529 bind — return to Cursor IDE"
                            f" · elapsed {elapsed_s:.0f}s"
                            f"{f' · {subject}' if subject else ''}"
                        )
                    elif closeout_json or parent_subj:
                        page_subject, page_body = _arc_outcome_line(
                            closeout_json,
                            label=args.label,
                            parent_harvest_subject=parent_subj,
                        )
                    else:
                        page_subject = f"ULG dispatch done — {args.label}"
                        page_body = (
                            f"thread {thread_id} · {outcome}"
                            f" · elapsed {elapsed_s:.0f}s"
                            f"{f' · worker {duration:.0f}s' if isinstance(duration, (int, float)) else ''}"
                            f"{f' · {model}' if model else ''}"
                            f"{f' · {subject}' if subject else ''}"
                        )
                    _page_operator(
                        subject=page_subject,
                        body=page_body,
                        tag="dispatch-done",
                    )
                return 0

            turn_count = snap.get("turn_count")
            thread_status = snap.get("thread_status")
            print(
                f"… waiting ({thread_status}, turns={turn_count})",
                flush=True,
            )
            time.sleep(_POLL_SLEEP_S)


if __name__ == "__main__":
    raise SystemExit(main())
