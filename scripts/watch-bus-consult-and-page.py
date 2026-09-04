#!/usr/bin/env python3
"""Poll agent-bus until a consult reply lands; page operator once.

Prefer detached arm via scripts/watch-supervise.sh (a:32280) so Cursor Shell
abort cannot kill the poller. In-window wake = supervise tail of the log.

Usage:
  scripts/watch-supervise.sh start --label '6341-close' -- \\
    scripts/watch-bus-consult-and-page.py --thread 6341 --after-turn 54 --no-page
  scripts/watch-supervise.sh tail --label '6341-close'   # notify on 'consult complete'
  scripts/watch-bus-consult-tmux.sh --thread 6341 --after-turn 54 --label '6341 close-arc'
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

from bus_watch.poll import DEFAULT_MAX_HOURS, DEFAULT_WAIT_SLICE_S, sliced_wait_loop
from bus_watch.stall_pop import emit_stall_pop
from bus_watch.stall_predicate import stall_predicate
from bus_watch.state import write_state

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_DEFAULT_COMPLETION = "proof_reply_from"
_BUS_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)


def _token() -> str:
    with open(_MCP_YAML) as f:
        cfg = yaml.safe_load(f)
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus_client(token: str, *, timeout_s: float) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=timeout_s + 10.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _wait_reply(
    client: httpx.Client,
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str,
    wait_s: int,
    completion: str = _DEFAULT_COMPLETION,
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": wait_s,
        "completion": completion,
        "from_agent": from_agent,
    }
    resp = client.get(f"http://localhost/threads/{thread_id}/wait?{urlencode(params)}")
    resp.raise_for_status()
    return resp.json()


def _fetch_turn_subject(client: httpx.Client, thread_id: str, turn: int) -> str:
    resp = client.get(
        f"http://localhost/turns?{urlencode({'thread': thread_id, 'last': 5, 'compact': 'true'})}"
    )
    resp.raise_for_status()
    for row in resp.json().get("turns") or []:
        if row.get("turn_number") == turn:
            return str(row.get("subject") or "")
    return ""


def _load_scoreboard_body(uri: str) -> str:
    """Best-effort scoreboard read from local cortex share path."""
    text = uri.strip()
    if not text:
        return ""
    if text.startswith("cortex://"):
        rel = text[len("cortex://") :]
        path = Path("/mnt/torus/mcp-data/files") / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
    path = Path(text)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


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
    parser.add_argument("--thread", required=True, help="agent-bus thread id")
    parser.add_argument(
        "--after-turn",
        type=int,
        required=True,
        help="wait for reply after this turn number",
    )
    parser.add_argument(
        "--from-agent",
        default="web-anthropic",
        help="qualifying reply from_agent (default: web-anthropic)",
    )
    parser.add_argument(
        "--label",
        default="bus consult",
        help="short label for pager subject / heartbeat",
    )
    parser.add_argument(
        "--answer-uri",
        default="",
        help="optional cortex URI to include in pager body",
    )
    parser.add_argument(
        "--scoreboard-uri",
        default="",
        help="optional scoreboard cortex URI for mission_open fold",
    )
    parser.add_argument(
        "--closeout-body-file",
        default="",
        help="optional closeout body file for park/harvest stall context",
    )
    parser.add_argument(
        "--no-page",
        action="store_true",
        help="print closeout only; do not SMS",
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
        help=f"orphan ceiling; exit non-zero when exceeded (default {DEFAULT_MAX_HOURS:g})",
    )
    parser.add_argument(
        "--state-file",
        default="",
        help="durable JSON status path (supervise sets this)",
    )
    args = parser.parse_args()

    thread_id = str(args.thread).strip()
    from_agent = str(args.from_agent).strip()
    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    token = _token()
    slice_s = max(1.0, float(args.wait_slice_seconds))
    scoreboard_body = _load_scoreboard_body(str(args.scoreboard_uri))
    closeout_body = ""
    if str(args.closeout_body_file).strip():
        closeout_path = Path(args.closeout_body_file)
        if closeout_path.is_file():
            closeout_body = closeout_path.read_text(encoding="utf-8")

    print(
        f"watching thread={thread_id} after_turn={args.after_turn} "
        f"from_agent={from_agent!r} label={args.label!r} "
        f"wait_slice_s={slice_s:g} max_hours={args.max_hours:g}",
        flush=True,
    )
    if state_path is not None:
        write_state(
            state_path,
            status="armed",
            thread=thread_id,
            after_turn=args.after_turn,
            from_agent=from_agent,
            label=args.label,
        )

    client = _bus_client(token, timeout_s=slice_s)
    predicate_unmet_slices = 0
    last_turn_count: int | None = None
    last_stall_key: tuple[str, int | None] | None = None

    def wait_once(wait_s: int) -> dict[str, Any]:
        return _wait_reply(
            client,
            thread_id=thread_id,
            after_turn=args.after_turn,
            from_agent=from_agent,
            wait_s=wait_s,
        )

    def on_transport(exc: BaseException) -> None:
        nonlocal client
        print(
            f"… bus transport error ({type(exc).__name__}: {exc}); reconnecting",
            flush=True,
        )
        client.close()
        client = _bus_client(token, timeout_s=slice_s)

    def on_incomplete(snap: dict[str, Any]) -> None:
        nonlocal predicate_unmet_slices, last_turn_count, last_stall_key
        status = snap.get("status")
        turn_count = snap.get("turn_count")
        thread_status = snap.get("thread_status")
        turn_count_i: int | None
        if isinstance(turn_count, int):
            turn_count_i = turn_count
        else:
            try:
                turn_count_i = int(turn_count) if turn_count is not None else None
            except (TypeError, ValueError):
                turn_count_i = None

        if status == "predicate_unmet":
            if turn_count_i is not None and turn_count_i == last_turn_count:
                predicate_unmet_slices += 1
            else:
                predicate_unmet_slices = 1
            print(
                f"… predicate_unmet ({thread_status}, turns={turn_count}) — "
                "chrome-only or envelope stub; keep polling",
                flush=True,
            )
        else:
            predicate_unmet_slices = 0
            print(
                f"… waiting ({thread_status}, turns={turn_count})",
                flush=True,
            )
        last_turn_count = turn_count_i

        should_pop, reason = stall_predicate(
            thread_snapshot=snap,
            scoreboard_body=scoreboard_body,
            closeout_body=closeout_body,
            wait_slice_s=slice_s,
            predicate_unmet_slices=predicate_unmet_slices,
            last_turn_count=last_turn_count,
        )
        if should_pop and reason:
            stall_key = (reason, turn_count_i)
            if stall_key != last_stall_key:
                emit_stall_pop(reason)
                last_stall_key = stall_key

    def on_complete(snap: dict[str, Any]) -> int:
        reply_turn = int(snap.get("qualifying_reply_turn") or 0)
        try:
            subject = _fetch_turn_subject(client, thread_id, reply_turn)
        except _BUS_TRANSPORT_ERRORS as exc:
            print(
                f"… subject fetch transport error ({type(exc).__name__}); "
                "will still emit consult complete",
                flush=True,
            )
            subject = ""
        print(
            f"consult complete turn={reply_turn} subject={subject!r} "
            f"thread_status={snap.get('thread_status')}",
            flush=True,
        )
        if state_path is not None:
            write_state(
                state_path,
                status="complete",
                qualifying_reply_turn=reply_turn,
                subject=subject,
            )
        if not args.no_page:
            page_subject = f"ULG consult done — {args.label}"
            page_body = (
                f"thread {thread_id} · turn {reply_turn}"
                f"{f' · {subject}' if subject else ''}"
            )
            if args.answer_uri:
                page_body += f" · {args.answer_uri}"
            _page_operator(
                subject=page_subject,
                body=page_body,
                tag="consult-done",
            )
        return 0

    try:
        return sliced_wait_loop(
            wait_once=wait_once,
            is_complete=lambda s: bool(s.get("complete")),
            on_incomplete=on_incomplete,
            on_complete=on_complete,
            transport_errors=_BUS_TRANSPORT_ERRORS,
            on_transport_error=on_transport,
            wait_slice_s=slice_s,
            max_hours=float(args.max_hours),
            state_file=state_path,
            heartbeat_label=str(args.label),
            thread_id=thread_id,
            after_turn=args.after_turn,
        )
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
