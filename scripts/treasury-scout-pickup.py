#!/usr/bin/env python3
"""Pickup SCOUT_CLOSEOUT on treasury-scout-handoff → intel.json + trader wake + GO + APPLY.

Defers intent selection to Grok (primary_symbol / intel deltas). Posts GO on trader house 9740.

Usage:
  scripts/treasury-scout-pickup.py --thread 10447
  scripts/treasury-scout-pickup.py --thread 10447 --turn 2 --dry-run
  scripts/treasury-scout-pickup.py --no-go   # wake only, skip 9740 GO
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

_REPO = Path(__file__).resolve().parents[1]
_CLAUDEBURST = Path(os.environ.get("CLAUDEBURST_ROOT", "/mnt/torus/projects/claudeburst"))
_INTEL = _CLAUDEBURST / "scripts.local" / "output" / "trader" / "intel.json"
_WAKE_JSON = _CLAUDEBURST / "scripts.local" / "output" / "trader" / "last_wake.json"
_PYTHON = Path(os.environ.get("UNIVERSAL_PYTHON", Path.home() / ".venvs/universal/bin/python"))
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_DEFAULT_THREAD = "10447"
_TRADER_HOUSE = os.environ.get("TREASURY_TRADER_THREAD", "9740")

_DELTA_RE = re.compile(r"([A-Z][A-Z0-9]+)([+-]\d+)")


def _fire_enabled() -> bool:
    """Treasury pickup arms relay by default; override with PERPS_TRADER_FIRE=false."""

    raw = os.getenv("PERPS_TRADER_FIRE")
    if raw is None:
        raw = os.getenv("TREASURY_SCOUT_AUTOFIRE", "true")
    return raw.strip().lower() == "true"


def _token() -> str:
    cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8"))
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=30.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _parse_closeout(body: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("Sidecar:") or line.startswith("TYPE:"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            fields[key.strip().lower()] = val.strip()
    hold = fields.get("hold", "false").lower() in ("true", "1", "yes")
    hold_go = fields.get("hold_go", "false").lower() in ("true", "1", "yes")
    deltas_line = fields.get("deltas", "")
    deltas: dict[str, dict[str, Any]] = {}
    for match in _DELTA_RE.finditer(deltas_line):
        sym, delta_s = match.group(1), match.group(2)
        deltas[sym] = {"delta": int(delta_s), "note": "", "url": None}
    for chunk in re.split(r";", deltas_line):
        chunk = chunk.strip()
        m = re.match(r"([A-Z][A-Z0-9/]+)\s+0\b", chunk)
        if m:
            for sym in m.group(1).split("/"):
                deltas.setdefault(sym, {"delta": 0, "note": "", "url": None})
    primary_symbol = fields.get("primary_symbol") or fields.get("primary") or ""
    if not primary_symbol and fields.get("recommend_go"):
        primary_symbol = fields.get("recommend_go", "").split()[0]
    primary_symbol = primary_symbol.strip().upper()
    primary_intent = fields.get("primary_intent", "").strip()
    scale = fields.get("scale", "").strip().lower()
    if scale not in ("probe", "quarter", "full", "max"):
        scale = ""
    scale_in = fields.get("scale_in", "false").lower() in ("true", "1", "yes")
    honor_clip: bool | None = None
    if "honor_clip" in fields:
        honor_clip = fields.get("honor_clip", "").lower() in ("true", "1", "yes")
    clip_policy = fields.get("clip", "").strip().lower()
    if clip_policy in ("honor", "apply"):
        honor_clip = True
    elif clip_policy in ("bypass", "waive", "none"):
        honor_clip = False
    size_usdc_raw = fields.get("size_usdc", "").strip()
    size_usdc: float | None = None
    if size_usdc_raw:
        try:
            size_usdc = float(size_usdc_raw.replace("$", "").replace(",", ""))
        except ValueError:
            size_usdc = None
    return {
        "hold": hold,
        "hold_go": hold_go,
        "scale": scale,
        "scale_in": scale_in,
        "honor_clip": honor_clip,
        "size_usdc": size_usdc,
        "deltas": deltas,
        "deltas_line": deltas_line,
        "so_what": fields.get("so_what", ""),
        "intel_path": fields.get("intel", ""),
        "brief_path": fields.get("brief", ""),
        "primary_symbol": primary_symbol,
        "primary_intent": primary_intent,
        "confidence": fields.get("confidence", "").lower(),
    }


def _find_closeout_turn(client: httpx.Client, thread: str, turn: int | None) -> dict[str, Any]:
    if turn is not None:
        r = client.get(
            "http://localhost/turns/by-number",
            params={"thread": thread, "turn_number": str(turn)},
        )
        r.raise_for_status()
        return r.json()
    r = client.get("http://localhost/turns", params={"thread": thread, "last": 30})
    r.raise_for_status()
    for t in reversed(r.json().get("turns") or []):
        body = str(t.get("body") or "")
        subj = str(t.get("subject") or "")
        author = t.get("from_agent") or t.get("from")
        if author != "gotgrok":
            continue
        if "SCOUT_CLOSEOUT" not in subj and "SCOUT_CLOSEOUT" not in body:
            continue
        if t.get("read_at") is not None:
            continue
        return t
    raise SystemExit(f"No unread SCOUT_CLOSEOUT from gotgrok on thread {thread}")


def _write_intel(parsed: dict[str, Any], fetched_at: str) -> None:
    payload: dict[str, Any] = {
        "fetched_at": fetched_at,
        "source": "grok-bot-reconstructed",
        "hold": parsed["hold"],
        "deltas": parsed["deltas"],
    }
    if parsed.get("scale"):
        payload["scale"] = parsed["scale"]
    if parsed.get("scale_in"):
        payload["scale_in"] = True
    if parsed.get("honor_clip") is not None:
        payload["honor_clip"] = parsed["honor_clip"]
    if parsed.get("size_usdc"):
        payload["size_usdc"] = parsed["size_usdc"]
    _INTEL.parent.mkdir(parents=True, exist_ok=True)
    _INTEL.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run_wake() -> tuple[int, str]:
    proc = subprocess.run(
        [_PYTHON, "-m", "trader", "wake"],
        cwd=_CLAUDEBURST,
        env={**os.environ, "PYTHONPATH": "scripts.local"},
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _fundable_intents() -> list[dict[str, Any]]:
    if not _WAKE_JSON.is_file():
        return []
    snap = json.loads(_WAKE_JSON.read_text(encoding="utf-8"))
    decision = snap.get("decision") if isinstance(snap.get("decision"), dict) else {}
    intents = decision.get("intents") if isinstance(decision.get("intents"), list) else []
    return [i for i in intents if isinstance(i, dict) and i.get("fundable")]


def _select_go_intent(parsed: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if parsed.get("hold") or parsed.get("hold_go"):
        return None, "hold"

    intents = _fundable_intents()
    if not intents:
        return None, "no_fundable_intents"

    primary_intent = parsed.get("primary_intent") or ""
    if primary_intent:
        for intent in intents:
            if intent.get("intent_id") == primary_intent:
                return intent, "grok_primary_intent"

    primary_symbol = parsed.get("primary_symbol") or ""
    if primary_symbol:
        for intent in intents:
            if intent.get("symbol") == primary_symbol:
                return intent, "grok_primary_symbol"

    def _rank(intent: dict[str, Any]) -> tuple[int, str]:
        sym = str(intent.get("symbol") or "")
        delta = int(parsed.get("deltas", {}).get(sym, {}).get("delta") or 0)
        return (-delta, sym)

    ranked = sorted(intents, key=_rank)
    if ranked[0] and _rank(ranked[0])[0] < 0:
        return None, "best_intent_still_demoted"
    return ranked[0], "grok_intel_rank"


def _summarize_wake() -> str:
    if not _WAKE_JSON.is_file():
        return "last_wake.json missing"
    snap = json.loads(_WAKE_JSON.read_text(encoding="utf-8"))
    gate = snap.get("gate") if isinstance(snap.get("gate"), dict) else {}
    decision = snap.get("decision") if isinstance(snap.get("decision"), dict) else {}
    intents = decision.get("intents") if isinstance(decision.get("intents"), list) else []
    lines = [
        f"fetched_at={snap.get('fetched_at')}",
        f"gate open={gate.get('open')} powder={gate.get('powder_usdc')} clip={gate.get('clip_usdc')}",
        f"decision={decision.get('action')} fire_path={decision.get('fire_path')}",
        f"watch={decision.get('watch')}",
    ]
    for intent in intents[:6]:
        if not isinstance(intent, dict):
            continue
        lines.append(
            f"intent {intent.get('intent_id')} {intent.get('side')} "
            f"size={intent.get('size_usdc')} fundable={intent.get('fundable')}"
        )
    return "\n".join(lines)


def _bus_send(
    client: httpx.Client,
    *,
    thread: str,
    to: str,
    after_turn: int | None,
    subject: str,
    body: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thread": str(thread),
        "to": to,
        "from": "cursor",
        "subject": subject,
        "body": body,
        "status": "open",
    }
    if after_turn is not None:
        payload["after_turn"] = after_turn
    r = client.post("http://localhost/turns", json=payload)
    r.raise_for_status()
    return r.json()


def _relay_fire(intent: dict[str, Any]) -> tuple[int, str]:
    """POST :8891/commands/enter-symbol when fire is armed."""

    fire_path = _CLAUDEBURST / "scripts.local" / "output" / "trader" / ".relay_intent.json"
    fire_path.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": "scripts.local"}
    if _fire_enabled():
        env["PERPS_TRADER_FIRE"] = "true"
    proc = subprocess.run(
        [_PYTHON, "-m", "trader", "fire", "--intent", str(fire_path)],
        cwd=_CLAUDEBURST,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _post_go(
    client: httpx.Client,
    intent: dict[str, Any],
    *,
    rationale: str,
    selection: str,
) -> dict[str, Any]:
    intent_id = str(intent.get("intent_id") or "")
    symbol = str(intent.get("symbol") or "")
    body = (
        f"TYPE: GO\n"
        f"source: grok-defer\n"
        f"selection: {selection}\n"
        f"intent_id: {intent_id}\n"
        f"symbol: {symbol}\n"
        f"side: {intent.get('side')}\n"
        f"size_usdc: {intent.get('size_usdc')}\n"
        f"rationale: {rationale}\n"
        f"fire_path: relay when PERPS_TRADER_FIRE=true"
    )
    return _bus_send(
        client,
        thread=_TRADER_HOUSE,
        to="cursor",
        after_turn=None,
        subject=f"GO {intent_id}",
        body=body,
    )


def _mark_read(client: httpx.Client, thread: str, through_turn: int) -> None:
    r = client.patch(
        f"http://localhost/threads/{thread}/turns/read-state",
        json={"through_turn": through_turn, "agent": "cursor"},
    )
    r.raise_for_status()


def _latest_turn_number(client: httpx.Client, thread: str) -> int:
    r = client.get("http://localhost/turns", params={"thread": thread, "last": 1})
    r.raise_for_status()
    turns = r.json().get("turns") or []
    if not turns:
        return 0
    return int(turns[-1].get("turn_number") or 0)


def _mark_cursor_inbox_read(client: httpx.Client, thread: str) -> None:
    """Avoid 409 unread_turns_exist when cursor posts APPLY after CHECKPOINTs."""

    through = _latest_turn_number(client, thread)
    if through > 0:
        _mark_read(client, thread, through)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", default=_DEFAULT_THREAD)
    parser.add_argument("--turn", type=int, default=None, help="specific closeout turn")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-go", action="store_true", help="skip 9740 GO post")
    parser.add_argument(
        "--no-fire",
        action="store_true",
        help="skip relay fire even when PERPS_TRADER_FIRE=true",
    )
    parser.add_argument(
        "--relay-only",
        action="store_true",
        help="relay fire fundable intent from last_wake (no wake); optional --symbol",
    )
    parser.add_argument("--symbol", default="", help="symbol for --relay-only")
    args = parser.parse_args()

    token = _token()
    client = _client(token)

    if args.relay_only:
        symbol = args.symbol.strip().upper()
        intents = _fundable_intents()
        if symbol:
            intents = [i for i in intents if str(i.get("symbol") or "").upper() == symbol]
        if not intents:
            print("relay-only: no fundable intent", file=sys.stderr, flush=True)
            return 1
        go_intent = intents[0]
        fire_line = "FIRE skipped"
        if not args.no_fire and _fire_enabled():
            code, fire_out = _relay_fire(go_intent)
            print(fire_out, flush=True)
            fire_line = (
                f"FIRE exit={code} intent={go_intent.get('intent_id')}"
                if code == 0
                else f"FIRE failed exit={code}"
            )
        elif not args.no_fire:
            fire_line = "FIRE skipped (PERPS_TRADER_FIRE not true)"
        print(fire_line, flush=True)
        after = _latest_turn_number(client, args.thread)
        apply_body = (
            f"TYPE: APPLY\n\nRelay-only pickup (treasury-scout-pickup.py --relay-only).\n\n"
            f"intent: {go_intent.get('intent_id')} {go_intent.get('side')} "
            f"size={go_intent.get('size_usdc')}\n"
            f"{fire_line}\n"
        )
        _mark_cursor_inbox_read(client, args.thread)
        _bus_send(
            client,
            thread=args.thread,
            to="gotgrok",
            after_turn=after if after > 0 else None,
            subject="APPLY — relay-only fire",
            body=apply_body,
        )
        print(f"APPLY relay-only posted after_turn={after}", flush=True)
        return 0

    turn = _find_closeout_turn(client, args.thread, args.turn)
    turn_no = int(turn["turn_number"])
    body = str(turn.get("body") or "")
    parsed = _parse_closeout(body)
    fetched_at = str(turn.get("created_at") or datetime.now(timezone.utc).isoformat())

    print(f"pickup thread={args.thread} turn={turn_no} hold={parsed['hold']}", flush=True)
    if parsed["hold"]:
        print("hold=true — skip wake", flush=True)
        if not args.dry_run:
            apply_body = (
                f"TYPE: APPLY\n\nPickup turn {turn_no}: hold=true — wake skipped.\n"
                f"so_what: {parsed['so_what']}"
            )
            _bus_send(
                client,
                thread=args.thread,
                to="gotgrok",
                after_turn=turn_no,
                subject=f"APPLY — turn {turn_no} held",
                body=apply_body,
            )
            _mark_read(client, args.thread, turn_no)
        return 0

    if args.dry_run:
        print(json.dumps(parsed, indent=2))
        return 0

    _write_intel(parsed, fetched_at)
    print(f"intel written {_INTEL}", flush=True)
    code, wake_out = _run_wake()
    print(wake_out, flush=True)
    if code != 0:
        print(f"trader wake exit {code}", file=sys.stderr, flush=True)
        return code

    go_intent, selection = _select_go_intent(parsed)
    go_line = "GO skipped"
    fire_line = "FIRE skipped"
    if go_intent and not args.no_go:
        _post_go(client, go_intent, rationale=parsed.get("so_what", ""), selection=selection)
        go_line = f"GO posted {_TRADER_HOUSE} {go_intent.get('intent_id')} ({selection})"
        print(go_line, flush=True)
        if not args.no_fire and _fire_enabled():
            code, fire_out = _relay_fire(go_intent)
            print(fire_out, flush=True)
            fire_line = (
                f"FIRE exit={code} intent={go_intent.get('intent_id')}"
                if code == 0
                else f"FIRE failed exit={code}"
            )
        elif not args.no_fire:
            fire_line = "FIRE skipped (PERPS_TRADER_FIRE not true)"
        print(fire_line, flush=True)
    elif not go_intent:
        print(f"GO skipped: {selection}", flush=True)

    summary = _summarize_wake()
    apply_body = (
        f"TYPE: APPLY\n\nPickup turn {turn_no} complete (treasury-scout-pickup.py).\n\n"
        f"intel: reconstructed → {_INTEL}\n"
        f"vm_intel: {parsed['intel_path'] or 'n/a'}\n\n"
        f"{summary}\n\n"
        f"{go_line}\n"
        f"{fire_line}\n"
        f"so_what: {parsed['so_what']}"
    )
    _mark_cursor_inbox_read(client, args.thread)
    _bus_send(
        client,
        thread=args.thread,
        to="gotgrok",
        after_turn=turn_no,
        subject=f"APPLY — turn {turn_no} scout closeout",
        body=apply_body,
    )
    _mark_read(client, args.thread, turn_no)
    print(f"APPLY posted after_turn={turn_no} marked_read through {turn_no}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
