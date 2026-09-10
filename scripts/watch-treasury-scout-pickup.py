#!/usr/bin/env python3
"""Long-poll treasury-scout-handoff for SCOUT_CLOSEOUT → mechanical pickup + relay.

Replaces IDE watcher relay (Fable #1 step 1): no human in the loop; PERPS_TRADER_FIRE armed.

Arm:
  scripts/watch-supervise.sh start --label treasury-scout-pickup -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-treasury-scout-pickup.py \\
    --thread 10447 --label treasury-scout-pickup
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml
from bus_watch.poll import DEFAULT_WAIT_SLICE_S
from bus_watch.state import paths_for, write_state

_REPO = Path(__file__).resolve().parents[1]
_PYTHON = Path(os.environ.get("UNIVERSAL_PYTHON", Path.home() / ".venvs/universal/bin/python"))
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_DEFAULT_THREAD = "10447"
_BUS_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)

_PICKUP_ENV = {
    "PERPS_TRADER_FIRE": "true",
    "TREASURY_SCOUT_AUTOFIRE": "true",
    "PERPS_ENTER_SYMBOL_ENABLED": os.environ.get("PERPS_ENTER_SYMBOL_ENABLED", "true"),
    "LIVE_TRADING": os.environ.get("LIVE_TRADING", "true"),
}


def _token() -> str:
    cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8"))
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _client(token: str, *, timeout_s: float) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=timeout_s + 10.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _latest_turn(client: httpx.Client, thread: str) -> int:
    r = client.get("http://localhost/turns", params={"thread": thread, "last": 1})
    r.raise_for_status()
    turns = r.json().get("turns") or []
    if not turns:
        return 0
    return int(turns[-1].get("turn_number") or 0)


def _wait_closeout(
    client: httpx.Client,
    *,
    thread: str,
    after_turn: int,
    wait_s: int,
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": wait_s,
        "completion": "proof_reply_from",
        "from_agent": "gotgrok",
    }
    r = client.get(f"http://localhost/threads/{thread}/wait?{urlencode(params)}")
    r.raise_for_status()
    return r.json()


def _run_pickup(thread: str, turn: int) -> int:
    env = {**os.environ, **_PICKUP_ENV}
    proc = subprocess.run(
        [
            str(_PYTHON),
            str(_REPO / "scripts" / "treasury-scout-pickup.py"),
            "--thread",
            thread,
            "--turn",
            str(turn),
        ],
        cwd=_REPO,
        env=env,
        check=False,
    )
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", default=_DEFAULT_THREAD)
    parser.add_argument("--label", default="treasury-scout-pickup")
    parser.add_argument("--after-turn", type=int, default=0, help="0 = latest on thread")
    parser.add_argument("--wait-slice-seconds", type=float, default=DEFAULT_WAIT_SLICE_S)
    parser.add_argument("--max-hours", type=float, default=0.0, help="0 = run forever")
    parser.add_argument("--state-file", default="")
    args = parser.parse_args()

    thread = str(args.thread).strip()
    label = str(args.label).strip()
    token = _token()
    slice_s = max(1.0, float(args.wait_slice_seconds))
    state_path = Path(args.state_file) if str(args.state_file).strip() else None
    if state_path is None:
        state_path = paths_for(label).state_file

    client = _client(token, timeout_s=slice_s)
    after_turn = int(args.after_turn)
    if after_turn <= 0:
        after_turn = _latest_turn(client, thread)

    print(
        f"treasury-scout poller thread={thread} after_turn={after_turn} "
        f"label={label} PERPS_TRADER_FIRE=true",
        flush=True,
    )
    write_state(
        state_path,
        status="polling",
        thread=thread,
        after_turn=after_turn,
        label=label,
    )

    def wait_once(wait_s: int) -> dict[str, Any]:
        return _wait_closeout(client, thread=thread, after_turn=after_turn, wait_s=wait_s)

    def on_transport(exc: BaseException) -> None:
        nonlocal client
        print(f"bus transport error ({type(exc).__name__}: {exc}); reconnecting", flush=True)
        client.close()
        client = _client(token, timeout_s=slice_s)

    started = time.monotonic()
    max_s = float(args.max_hours) * 3600.0 if float(args.max_hours) > 0 else 0.0
    slice_i = max(1, int(slice_s))

    while True:
        elapsed = time.monotonic() - started
        if max_s > 0 and elapsed >= max_s:
            write_state(state_path, status="expired", elapsed_s=round(elapsed, 1))
            return 2
        print(
            f"… heartbeat label={label} thread={thread} after_turn={after_turn} "
            f"elapsed={elapsed:.0f}s",
            flush=True,
        )
        write_state(
            state_path,
            status="polling",
            thread=thread,
            after_turn=after_turn,
            label=label,
        )
        try:
            snap = wait_once(slice_i)
        except _BUS_TRANSPORT_ERRORS as exc:
            on_transport(exc)
            time.sleep(3.0)
            continue

        if not snap.get("complete"):
            continue

        turn = int(snap.get("qualifying_reply_turn") or 0)
        if turn <= after_turn:
            continue
        r = client.get(
            "http://localhost/turns/by-number",
            params={"thread": thread, "turn_number": str(turn)},
        )
        r.raise_for_status()
        turn_row = r.json()
        subj = str(turn_row.get("subject") or "")
        body = str(turn_row.get("body") or "")
        if "SCOUT_CLOSEOUT" not in subj and "SCOUT_CLOSEOUT" not in body:
            after_turn = turn
            continue
        print(f"SCOUT_CLOSEOUT turn={turn} — running pickup", flush=True)
        write_state(state_path, status="pickup", qualifying_reply_turn=turn)
        code = _run_pickup(thread, turn)
        after_turn = turn
        write_state(
            state_path,
            status="polling",
            after_turn=after_turn,
            last_pickup_exit=code,
            qualifying_reply_turn=turn,
        )
        print(f"pickup exit={code} — resume poll after_turn={after_turn}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
