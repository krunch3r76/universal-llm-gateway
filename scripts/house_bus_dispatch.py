"""Agent-bus helpers for house orchestration watchers.

Code work is ``team_dispatch``. This module posts the bus turn only.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_HOUSE_TAG = "house-orchestration-watcher"


def bus_token() -> str:
    cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8"))
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus_client(token: str, *, timeout_s: float = 30.0) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=timeout_s,
        headers={"Authorization": f"Bearer {token}"},
    )


def bus_reply(
    *,
    thread: str,
    subject: str,
    body: str,
    after_turn: int,
    from_agent: str = "cursor",
    to: str = "cursor",
) -> dict[str, Any]:
    token = bus_token()
    payload = {
        "thread": str(thread),
        "from": from_agent,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": int(after_turn),
    }
    with _bus_client(token) as client:
        resp = client.post("http://localhost/turns", json=payload)
        resp.raise_for_status()
        return resp.json()


def dispatch_cursor_eval(
    *,
    thread: str,
    subject: str,
    body: str,
    after_turn: int,
    contract: str = "investigate",
    workspace: str | None = None,
    from_agent: str = "cursor",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Write the house-watcher bus turn. Code work is team_dispatch, not an enqueue."""
    token = bus_token()
    tags = [_HOUSE_TAG]
    send_payload: dict[str, Any] = {
        "thread": str(thread),
        "from": from_agent,
        "to": "cursor",
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": int(after_turn),
        "tags": tags,
    }
    with _bus_client(token) as client:
        send_resp = client.post("http://localhost/threads/send", json=send_payload)
        send_resp.raise_for_status()
        send_result = send_resp.json()

    thread_obj = send_result.get("thread") or {}
    turn_obj = send_result.get("turn") or {}
    return {
        "thread": thread_obj,
        "turn": turn_obj,
        "contract": contract,
        "workspace": workspace,
        "request_id": request_id,
    }


def fetch_after_turn(thread: str) -> int:
    token = bus_token()
    with _bus_client(token, timeout_s=10.0) as client:
        resp = client.get(f"http://localhost/turns?thread={thread}&last=1&compact=true")
        resp.raise_for_status()
        turns = resp.json().get("turns") or []
        if turns:
            return int(turns[0].get("turn_number") or 0)
    return 0


def fetch_thread_turns(thread: str, last: int = 3) -> list[dict[str, Any]]:
    token = bus_token()
    with _bus_client(token, timeout_s=15.0) as client:
        resp = client.get(
            f"http://localhost/turns?thread={thread}&last={int(last)}&compact=true"
        )
        resp.raise_for_status()
        turns = resp.json().get("turns") or []
        return turns if isinstance(turns, list) else []


def request_id_for_trigger(trigger_key: str, line: str) -> str:
    digest = hashlib.sha256(f"{trigger_key}\n{line}".encode()).hexdigest()[:24]
    return f"house-watcher-{digest}"
