#!/usr/bin/env python3
"""Observe-only monitor for gotgrok SCOUT_CLOSEOUT on treasury-scout-handoff.

Scores each closeout against live-book / tape rubric (E6 tracking). Does not run pickup —
that is watch-treasury-scout-pickup.py.

Arm:
  scripts/watch-supervise.sh start --label treasury-scout-closeout -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-treasury-scout-closeout.py \\
    --thread 10447 --label treasury-scout-closeout
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml
from bus_watch.poll import DEFAULT_WAIT_SLICE_S
from bus_watch.state import paths_for, write_state

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_DEFAULT_THREAD = "10447"
_BUS_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)

_TAPE_MARKERS = re.compile(
    r"\b(h6|h6_pct|tape|vol_rank|hurst|data_freshness)\b|@\d+\.\d+",
    re.I,
)
_BOOK_UNREADABLE = re.compile(r"book unreadable|claudeburst not on", re.I)
_EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
_ACTION_ALERT_CLASSES = frozenset(
    {"deploy_add", "deploy_open", "deploy_sized", "rotate", "rotate_signal"}
)


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


def _fetch_turn(client: httpx.Client, thread: str, turn: int) -> dict[str, Any]:
    r = client.get(
        "http://localhost/turns/by-number",
        params={"thread": thread, "turn_number": str(turn)},
    )
    r.raise_for_status()
    return r.json()


def _field(body: str, name: str) -> str:
    prefix = f"{name}:"
    for line in body.splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip()
    return ""


def _perps_get(path: str) -> dict[str, Any]:
    host = os.environ.get("CLAUDEBURST_PERPS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("CLAUDEBURST_PERPS_PORT", "8891").strip() or "8891"
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=3.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} returned non-object")
    return data


def _venue_held_symbols() -> tuple[list[str], str | None]:
    try:
        positions = _perps_get("/positions")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, OSError) as exc:
        return [], str(exc)
    held = positions.get("positions") if isinstance(positions.get("positions"), list) else []
    syms = sorted(
        {
            str(row.get("symbol") or "").upper()
            for row in held
            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        }
    )
    return syms, None


def _truthy_field(body: str, name: str) -> bool:
    return _field(body, name).lower() in ("true", "1", "yes")


def _classify_action(body: str, venue_held: list[str]) -> dict[str, Any]:
    """Non-hold scout nominations — alert operator when Grok wants deploy/rotate."""

    hold = _truthy_field(body, "hold")
    hold_go = _truthy_field(body, "hold_go")
    scale_in = _truthy_field(body, "scale_in")
    primary = _field(body, "primary_symbol").upper()
    scale = _field(body, "scale").lower()
    size_raw = _field(body, "size_usdc").lower()
    deltas = _field(body, "deltas")
    reasons: list[str] = []

    if hold or hold_go:
        return {
            "action_class": "hold_explicit",
            "action_alert": False,
            "reasons": ["hold_go" if hold_go else "hold"],
        }

    action_class = "hold_custody"
    if scale_in:
        action_class = "deploy_add"
        reasons.append("scale_in=true")
    if size_raw and size_raw not in ("null", "none", ""):
        if action_class == "hold_custody":
            action_class = "deploy_sized"
        reasons.append(f"size_usdc={size_raw}")
    if scale == "max" and not scale_in and primary in venue_held:
        reasons.append("scale=max_custody_hold")
    if primary and venue_held and primary not in venue_held:
        action_class = "rotate" if venue_held else "deploy_open"
        reasons.append(f"primary={primary} venue_held={venue_held}")
    elif primary and not venue_held:
        action_class = "deploy_open"
        reasons.append(f"flat_open primary={primary}")
    if re.search(r"\b[A-Z][A-Z0-9]+\-1\b", deltas):
        if action_class == "hold_custody":
            action_class = "rotate_signal"
        reasons.append("delta_demotion")

    action_alert = action_class in _ACTION_ALERT_CLASSES
    return {
        "action_class": action_class,
        "action_alert": action_alert,
        "reasons": reasons,
    }


def _page_operator(*, subject: str, body: str, tag: str) -> bool:
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    payload = {"subject": subject[:120], "body": body[:4000], "tag": tag[:40]}
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
    print(f"pager action_alert: {data}", flush=True)
    return ok


def _score_closeout(*, turn: int, subject: str, body: str) -> dict[str, Any]:
    so_what = _field(body, "so_what")
    primary = _field(body, "primary_symbol").upper()
    held, venue_err = _venue_held_symbols()
    book_unreadable = bool(_BOOK_UNREADABLE.search(body))
    cites_tape = bool(_TAPE_MARKERS.search(so_what))
    dual_fart_claim = bool(re.search(r"\bFART\b", so_what, re.I)) and "FART" not in held
    primary_ok = bool(primary and (not held or primary in held))
    e6_pass = cites_tape and not book_unreadable and primary_ok and not dual_fart_claim

    flags: list[str] = []
    if book_unreadable:
        flags.append("book_unreadable")
    if not cites_tape:
        flags.append("no_tape_markers_in_so_what")
    if dual_fart_claim:
        flags.append("stale_fart_claim")
    if primary and held and primary not in held:
        flags.append("primary_not_held")
    if venue_err:
        flags.append(f"venue_unreadable:{venue_err}")

    action = _classify_action(body, held)
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "turn": turn,
        "subject": subject,
        "primary_symbol": primary,
        "venue_held": held,
        "so_what_head": so_what[:160],
        "e6_pass": e6_pass,
        "flags": flags,
        "book_unreadable": book_unreadable,
        "cites_tape_markers": cites_tape,
        **action,
    }
    return row


def _append_ledger(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", default=_DEFAULT_THREAD)
    parser.add_argument("--label", default="treasury-scout-closeout")
    parser.add_argument("--after-turn", type=int, default=0, help="0 = latest on thread")
    parser.add_argument("--wait-slice-seconds", type=float, default=DEFAULT_WAIT_SLICE_S)
    parser.add_argument("--ledger", default="", help="default tmp/watchers/<label>.ledger.jsonl")
    parser.add_argument("--state-file", default="")
    args = parser.parse_args()

    thread = str(args.thread).strip()
    label = str(args.label).strip()
    token = _token()
    slice_s = max(1.0, float(args.wait_slice_seconds))
    paths = paths_for(label)
    state_path = Path(args.state_file) if str(args.state_file).strip() else paths.state_file
    ledger_path = (
        Path(args.ledger)
        if str(args.ledger).strip()
        else paths.directory / f"{paths.label}.ledger.jsonl"
    )

    client = _client(token, timeout_s=slice_s)
    after_turn = int(args.after_turn)
    if after_turn <= 0:
        after_turn = _latest_turn(client, thread)

    print(
        f"treasury-scout closeout monitor thread={thread} after_turn={after_turn} "
        f"ledger={ledger_path}",
        flush=True,
    )
    write_state(
        state_path,
        status="polling",
        thread=thread,
        after_turn=after_turn,
        label=label,
        ledger=str(ledger_path),
    )

    slice_i = max(1, int(slice_s))
    while True:
        write_state(
            state_path,
            status="polling",
            thread=thread,
            after_turn=after_turn,
            label=label,
            ledger=str(ledger_path),
        )
        try:
            snap = _wait_closeout(client, thread=thread, after_turn=after_turn, wait_s=slice_i)
        except _BUS_TRANSPORT_ERRORS as exc:
            print(f"bus transport error ({type(exc).__name__}: {exc}); reconnecting", flush=True)
            client.close()
            client = _client(token, timeout_s=slice_s)
            time.sleep(3.0)
            continue

        if not snap.get("complete"):
            continue

        turn = int(snap.get("qualifying_reply_turn") or 0)
        if turn <= after_turn:
            continue

        row = _fetch_turn(client, thread, turn)
        subj = str(row.get("subject") or "")
        body = str(row.get("body") or "")
        if "SCOUT_CLOSEOUT" not in subj and "SCOUT_CLOSEOUT" not in body:
            after_turn = turn
            continue

        score = _score_closeout(turn=turn, subject=subj, body=body)
        _append_ledger(ledger_path, score)
        flag_s = ",".join(score["flags"]) if score["flags"] else "ok"
        print(
            f"SCOUT_CLOSEOUT turn={turn} e6_pass={score['e6_pass']} flags={flag_s} "
            f"primary={score['primary_symbol']!r} venue={score['venue_held']} "
            f"action={score['action_class']} alert={score['action_alert']}",
            flush=True,
        )
        alerts_path = ledger_path.parent / f"{paths.label}.action-alerts.jsonl"
        if score.get("action_alert"):
            _append_ledger(alerts_path, score)
            print(
                f"ACTION_ALERT turn={turn} class={score['action_class']} "
                f"primary={score['primary_symbol']!r} reasons={score.get('reasons')}",
                flush=True,
            )
            if os.environ.get("TREASURY_SCOUT_ACTION_PAGER", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }:
                _page_operator(
                    subject=f"Treasury scout {score['action_class']} t{turn}",
                    body=(
                        f"thread 10447 turn {turn}\n"
                        f"class={score['action_class']}\n"
                        f"primary={score['primary_symbol']}\n"
                        f"so_what={score['so_what_head']}\n"
                        f"reasons={score.get('reasons')}"
                    ),
                    tag="treasury-scout",
                )
        write_state(
            state_path,
            status="polling",
            thread=thread,
            after_turn=turn,
            label=label,
            ledger=str(ledger_path),
            action_alerts=str(alerts_path),
            last_closeout=score,
            action_pending=bool(score.get("action_alert")),
        )
        after_turn = turn


if __name__ == "__main__":
    raise SystemExit(main())
