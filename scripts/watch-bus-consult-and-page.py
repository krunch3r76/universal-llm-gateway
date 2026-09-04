#!/usr/bin/env python3
"""Poll agent-bus until a consult reply lands; page operator once.

Usage:
  scripts/watch-bus-consult-tmux.sh --thread 6341 --after-turn 54 --label '6341 close-arc'
  scripts/watch-bus-consult-and-page.py --thread 6341 --after-turn 54 --from-agent cdp
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
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_WAIT_SECONDS = 55.0
_POLL_SLEEP_S = 2.0
_TRANSPORT_RETRY_SLEEP_S = 3.0
_DEFAULT_COMPLETION = "proof_reply_from"

# Bus recycle / UDS drop mid-wait (incident: agent-bus recycle during G6 watch).
_BUS_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)


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


def _wait_reply(
    client: httpx.Client,
    *,
    thread_id: str,
    after_turn: int,
    from_agent: str,
    completion: str = _DEFAULT_COMPLETION,
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": int(_WAIT_SECONDS),
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
        help="short label for pager subject",
    )
    parser.add_argument(
        "--answer-uri",
        default="",
        help="optional cortex URI to include in pager body",
    )
    parser.add_argument(
        "--no-page",
        action="store_true",
        help="print closeout only; do not SMS",
    )
    args = parser.parse_args()

    thread_id = str(args.thread).strip()
    from_agent = str(args.from_agent).strip()
    token = _token()
    print(
        f"watching thread={thread_id} after_turn={args.after_turn} "
        f"from_agent={from_agent!r} label={args.label!r}",
        flush=True,
    )

    client = _bus_client(token)
    try:
        while True:
            try:
                snap = _wait_reply(
                    client,
                    thread_id=thread_id,
                    after_turn=args.after_turn,
                    from_agent=from_agent,
                )
            except _BUS_TRANSPORT_ERRORS as exc:
                print(
                    f"… bus transport error ({type(exc).__name__}: {exc}); "
                    "reconnecting",
                    flush=True,
                )
                client.close()
                time.sleep(_TRANSPORT_RETRY_SLEEP_S)
                client = _bus_client(token)
                continue

            if snap.get("complete"):
                reply_turn = int(snap.get("qualifying_reply_turn") or 0)
                try:
                    subject = _fetch_turn_subject(client, thread_id, reply_turn)
                except _BUS_TRANSPORT_ERRORS as exc:
                    print(
                        f"… subject fetch transport error ({type(exc).__name__}); "
                        "reconnecting",
                        flush=True,
                    )
                    client.close()
                    time.sleep(_TRANSPORT_RETRY_SLEEP_S)
                    client = _bus_client(token)
                    continue
                print(
                    f"consult complete turn={reply_turn} subject={subject!r} "
                    f"thread_status={snap.get('thread_status')}",
                    flush=True,
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

            status = snap.get("status")
            turn_count = snap.get("turn_count")
            thread_status = snap.get("thread_status")
            if status == "predicate_unmet":
                print(
                    f"… predicate_unmet ({thread_status}, turns={turn_count}) — "
                    "chrome-only or envelope stub; keep polling",
                    flush=True,
                )
            else:
                print(
                    f"… waiting ({thread_status}, turns={turn_count})",
                    flush=True,
                )
            time.sleep(_POLL_SLEEP_S)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
