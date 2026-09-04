#!/usr/bin/env python3
"""UDS poll until path-sim leg completes — exit triggers Cursor harvest notification.

Reads/writes /tmp/path-sim-friction-27095-state.json. Loops agent-bus wait until
the active leg's from_agent replies, then writes harvest_ready and exits 0.

Usage:
  scripts/watch-path-sim-harvest.py
  scripts/watch-path-sim-harvest.py --phase R --thread 6395 --after-turn 5 --from-agent cdp
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

DEFAULT_STATE = Path("/tmp/path-sim-friction-27095-state.json")
LOG = Path("/tmp/path-sim-friction-27095-harvest-watch.log")
SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
WAIT_S = 55
POLL_GAP_S = 2.0
TRANSPORT_RETRY_SLEEP_S = 3.0
_DEFAULT_COMPLETION = "proof_reply_from"
_BUS_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def token() -> str:
    cfg = yaml.safe_load(open(Path.home() / ".gateway" / "mcp.yaml"))
    t = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not t:
        raise SystemExit("AGENT_BUS_TOKEN missing in ~/.gateway/mcp.yaml")
    return t


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2))


def page_operator(*, subject: str, body: str, tag: str) -> bool:
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        log("pager disabled (PAGER_NOTIFY_ENABLED=0)")
        return False
    payload = {"subject": subject[:120], "body": body[:4000], "tag": tag[:40]}
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            EMAIL_BRIDGE_SOCK,
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
        log(f"pager curl failed: {proc.stderr.strip()}")
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        log(f"pager bad response: {proc.stdout!r}")
        return False
    ok = str(data.get("status")) == "sent"
    log(f"pager: {data}")
    return ok


def wait_leg(
    client: httpx.Client,
    *,
    thread: str,
    after_turn: int,
    from_agent: str,
    completion: str = _DEFAULT_COMPLETION,
) -> dict[str, Any]:
    params = {
        "after_turn": after_turn,
        "wait": WAIT_S,
        "completion": completion,
        "from_agent": from_agent,
    }
    resp = client.get(
        f"http://localhost/threads/{thread}/wait?{urlencode(params)}"
    )
    resp.raise_for_status()
    return resp.json()


def resolve_poll(state: dict[str, Any], args: argparse.Namespace) -> tuple[str, int, str, str]:
    """Return (phase_label, thread, after_turn, from_agent)."""
    phase = args.phase or state.get("poll_phase") or state.get("phase") or "R"
    if phase in ("A_DONE", "Q_DONE"):
        phase = "R" if phase == "A_DONE" else "Q"
    if phase == "R_DONE":
        phase = "IMPL"
    if phase == "IMPL_DONE":
        phase = "R_AFTER"

    thread = str(
        args.thread
        or state.get("poll_thread")
        or state.get("coord_thread")
        or state.get("worker_thread")
        or "6395"
    )
    after_turn = int(args.after_turn or state.get("poll_after_turn") or state.get("after_turn") or 5)
    from_agent = str(args.from_agent or state.get("poll_from_agent") or state.get("from_agent") or "web-anthropic")

    # Phase defaults when state still says A_DONE but we're polling R
    if phase == "R":
        thread = str(args.thread or state.get("coord_thread") or "6395")
        after_turn = int(args.after_turn or state.get("poll_after_turn") or 5)
        from_agent = "web-anthropic"
    elif phase == "IMPL":
        thread = str(args.thread or state.get("worker_thread") or state.get("impl_thread") or thread)
        after_turn = int(args.after_turn or state.get("impl_after_turn") or 1)
        from_agent = "cursor-sdk"
    elif phase == "R_AFTER":
        thread = str(args.thread or state.get("worker_thread") or thread)
        after_turn = int(args.after_turn or state.get("rafter_after_turn") or 1)
        from_agent = "cursor-sdk"

    return phase, thread, after_turn, from_agent


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll path-sim leg until harvest")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--phase", choices=["Q", "A", "R", "IMPL", "R_AFTER"])
    parser.add_argument("--thread")
    parser.add_argument("--after-turn", type=int)
    parser.add_argument("--from-agent")
    parser.add_argument(
        "--page",
        action="store_true",
        help="SMS via email-bridge /pager/notify when harvest ready",
    )
    parser.add_argument(
        "--friction-id",
        default="27095",
        help="friction assertion id for pager copy",
    )
    args = parser.parse_args()

    state = load_state(args.state)
    phase, thread, after_turn, from_agent = resolve_poll(state, args)

    log(f"HARVEST_WATCH start phase={phase} thread={thread} after_turn={after_turn} from={from_agent}")

    def _new_client() -> httpx.Client:
        return httpx.Client(
            transport=httpx.HTTPTransport(uds=SOCK),
            timeout=WAIT_S + 15.0,
            headers={"Authorization": f"Bearer {token()}"},
        )

    client = _new_client()
    try:
        while True:
            try:
                data = wait_leg(
                    client, thread=thread, after_turn=after_turn, from_agent=from_agent
                )
            except _BUS_TRANSPORT_ERRORS as exc:
                log(
                    f"bus transport error ({type(exc).__name__}: {exc}); reconnecting"
                )
                client.close()
                time.sleep(TRANSPORT_RETRY_SLEEP_S)
                client = _new_client()
                continue
            complete = bool(data.get("complete"))
            qturn = data.get("qualifying_reply_turn")
            log(
                f"wait complete={complete} status={data.get('status')} qturn={qturn}"
            )
            if complete:
                done_phase = f"{phase}_DONE"
                state.update(
                    {
                        "phase": done_phase,
                        "poll_phase": None,
                        "harvest_ready": True,
                        "harvest_at": time.time(),
                        "qualifying_reply_turn": qturn,
                        "poll_thread": thread,
                        "poll_after_turn": after_turn,
                        "poll_from_agent": from_agent,
                        "note": f"HARVEST_READY — run harvest for {done_phase}",
                    }
                )
                save_state(args.state, state)
                log(f"HARVEST_READY phase={done_phase} thread={thread} turn={qturn}")
                print(f"\n=== HARVEST_READY phase={done_phase} thread={thread} ===", flush=True)
                if args.page:
                    page_operator(
                        subject=f"path-sim harvest — friction a:{args.friction_id} · 529",
                        body=(
                            f"R-admit ready · friction a:{args.friction_id} "
                            f"(CDP 529 retry bind) · thread {thread} turn {qturn} · "
                            f"say harvest in Cursor"
                        ),
                        tag="path-sim-harvest",
                    )
                return 0
            if data.get("status") == "predicate_unmet":
                log(
                    "predicate_unmet — chrome-only or envelope stub; keep polling"
                )
            time.sleep(POLL_GAP_S)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
