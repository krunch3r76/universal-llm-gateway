#!/usr/bin/env python3
"""Liaison tick — one lean, read-only digest per wake for a continuity-root seat.

The liaison (attended or autonomous register) must not read the bus linearly:
each wake it consumes ONE digest line and decides (harvest · fold · dispatch ·
checkpoint · hop · park). This script produces that line.

Modes:
  --once                 print the digest JSON and exit (dogfood / manual tick)
  --loop                 emit ``AGENT_LOOP_TICK_liaison <json>`` when a watched
                         lane changed or the heartbeat elapsed; the IDE tab arms
                         it as a monitored background shell (``/loop`` local
                         mechanism) so the sentinel wakes the seat.

Digest contents: root + child lanes (lineage), per-lane turn/unread counters,
terminal-class last subjects, unread TOC scoped to those lanes, completed
watcher state files not yet relayed, fleet health, and a context-budget
governor (ticks · estimated tokens · stop class). Budget is an *estimate*
carried with its basis — the seat treats ``stop_class`` as a designed stop,
never as proof of exhaustion.

Bus access mirrors scripts/watch-cursor-bridge-inbox.py (UDS + AGENT_BUS_TOKEN).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_STARGATE_HEALTH = os.environ.get(
    "LIAISON_STARGATE_HEALTH", "http://localhost:9999/health"
)
_GIW_HEALTH = os.environ.get("LIAISON_GIW_HEALTH", "http://127.0.0.1:8091/health")
_WATCH_DIR = _REPO / "tmp" / "watchers"
_SENTINEL = "AGENT_LOOP_TICK_liaison"
_TERMINAL_RE = re.compile(
    r"CLOSEOUT|status:done|status:failed|status:needs-attended|SCORE_RESURFACE|"
    r"stall-pop|PARKED|FAILED|CHECKPOINT|BRIDGE_ACK",
    re.I,
)
# Per-tick fixed overhead the seat spends reading the digest and deciding.
_TICK_OVERHEAD_TOKENS = 3000
_MAX_LANES = 25
_SUBJECT_CAP = 120
_WORKER_RE = re.compile(r"Worker thread `(\d+)`")


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _token() -> str:
    cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8")) or {}
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus() -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=20.0,
        headers={"Authorization": f"Bearer {_token()}"},
        base_url="http://localhost",
    )


def _get(client: httpx.Client, path: str, **params: Any) -> dict[str, Any] | None:
    try:
        r = client.get(path, params={k: v for k, v in params.items() if v is not None})
    except httpx.HTTPError as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"[:200]}
    if r.status_code >= 400:
        return {"_error": f"http_{r.status_code}", "_body": r.text[:200]}
    try:
        data = r.json()
    except ValueError:
        return {"_error": "non_json"}
    return data if isinstance(data, dict) else {"_list": data}


def _lane_row(t: dict[str, Any]) -> dict[str, Any]:
    subject = str(t.get("last_subject") or "")[:_SUBJECT_CAP]
    return {
        "id": str(t.get("id")),
        "slug": t.get("slug"),
        "status": t.get("status"),
        "lifecycle": t.get("bus_lifecycle_state"),
        "lane_role": t.get("lane_role"),
        "turns": t.get("turn_count"),
        "unread": t.get("unread_count"),
        "last_from": t.get("last_turn_from"),
        "last_subject": subject,
        "terminal": bool(_TERMINAL_RE.search(subject)),
        "updated_at": t.get("updated_at"),
    }


def _linked_worker_ids(client: httpx.Client, root: str) -> set[str]:
    """Worker threads the root's admit turns point at (siblings, not children)."""
    turns = _get(client, "/turns", thread=root, last=40) or {}
    ids: set[str] = set()
    for turn in turns.get("turns") or []:
        for m in _WORKER_RE.finditer(str(turn.get("body") or "")):
            ids.add(m.group(1))
    return ids


def _child_lanes(client: httpx.Client, root: str) -> list[dict[str, Any]]:
    """Children, grandchildren, and admit-linked workers of ``root``; bounded."""
    listing = _get(client, "/threads", status="all", last=400) or {}
    rows = listing.get("threads") or []
    by_id = {str(t.get("id")): t for t in rows}
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for t in rows:
        parent = t.get("parent_thread")
        if parent:
            by_parent.setdefault(str(parent), []).append(t)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    frontier = [root]
    while frontier and len(out) < _MAX_LANES:
        nxt: list[str] = []
        for parent in frontier:
            for t in by_parent.get(parent, []):
                tid = str(t.get("id"))
                if tid in seen:
                    continue
                seen.add(tid)
                out.append(_lane_row(t))
                nxt.append(tid)
        frontier = nxt
    for tid in _linked_worker_ids(client, root) - seen:
        row = by_id.get(tid) or _get(client, f"/threads/{tid}")
        if row and "_error" not in row:
            seen.add(tid)
            out.append(
                {**_lane_row(row), "lane_role": row.get("lane_role") or "linked_worker"}
            )
    out.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return out[:_MAX_LANES]


def _unread_toc(client: httpx.Client, lane_ids: set[str]) -> list[dict[str, Any]]:
    toc = _get(client, "/turns/unread-toc", to="cursor", limit=100) or {}
    if "_error" in toc:
        return [{"_error": toc["_error"]}]
    rows = toc.get("threads") or toc.get("items") or toc.get("toc") or []
    out = []
    for row in rows:
        tid = str(row.get("thread") or row.get("thread_id") or row.get("id") or "")
        if tid in lane_ids:
            out.append(
                {
                    "thread": tid,
                    "unread": row.get("unread_count") or row.get("unread"),
                    "last_subject": str(row.get("last_subject") or "")[:_SUBJECT_CAP],
                    "last_activity_at": row.get("last_activity_at"),
                }
            )
    return out


def _watchers(state: dict[str, Any], lane_ids: set[str]) -> list[dict[str, Any]]:
    """Completed watcher state files in scope (lane match or newer than liaison birth)."""
    relayed = set(state.get("relayed_watchers") or [])
    born = float(state.get("born_epoch") or 0.0)
    out = []
    for path in sorted(_WATCH_DIR.glob("*.state.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        in_scope = str(data.get("thread") or "") in lane_ids or mtime >= born
        if data.get("status") == "complete" and path.name not in relayed and in_scope:
            out.append(
                {
                    "file": path.name,
                    "thread": data.get("thread"),
                    "label": data.get("label"),
                }
            )
    return out[:10]


def _health(url: str) -> str:
    try:
        r = httpx.get(url, timeout=3.0)
    except httpx.HTTPError as exc:
        return f"down:{type(exc).__name__}"
    return "ok" if r.status_code < 400 else f"http_{r.status_code}"


def _load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fingerprint(root: dict[str, Any], lanes: list[dict[str, Any]]) -> str:
    key = [(root.get("id"), root.get("turn_count"), root.get("status"))]
    key += [
        (lane["id"], lane["turns"], lane["status"], lane["lifecycle"]) for lane in lanes
    ]
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def build_digest(
    root_id: str, state: dict[str, Any], *, register: str, budget_tokens: int
) -> dict[str, Any]:
    """Assemble one digest; mutates ``state`` counters (ticks, est_tokens)."""
    with _bus() as client:
        root = _get(client, f"/threads/{root_id}") or {}
        lanes = _child_lanes(client, root_id) if "_error" not in root else []
        lane_ids = {root_id, *(lane["id"] for lane in lanes)}
        unread = _unread_toc(client, lane_ids)

    fp = _fingerprint(root, lanes)
    changed = fp != state.get("fingerprint")
    ticks = int(state.get("ticks") or 0) + 1
    digest: dict[str, Any] = {
        "ts": _utcnow(),
        "root": {
            "id": root_id,
            "slug": root.get("slug"),
            "turns": root.get("turn_count"),
            "unread": root.get("unread_count"),
            "last_subject": str(root.get("last_subject") or "")[:_SUBJECT_CAP],
            "error": root.get("_error"),
        },
        "register": register,
        "lanes": lanes,
        "attention": [
            lane for lane in lanes if lane["terminal"] or (lane["unread"] or 0) > 0
        ],
        "unread_toc": unread,
        "watchers_complete_unrelayed": _watchers(state, lane_ids),
        "fleet": {"stargate": _health(_STARGATE_HEALTH), "giw": _health(_GIW_HEALTH)},
        "changed_since_last_tick": changed,
        "fingerprint": fp,
    }
    est = (
        int(state.get("est_tokens") or 0)
        + _TICK_OVERHEAD_TOKENS
        + len(json.dumps(digest)) // 4
    )
    pct = round(100.0 * est / max(budget_tokens, 1), 1)
    last_cp_tick = int(state.get("last_cp_tick") or 0)
    stop_class = "CONTEXT_BUDGET" if pct >= 80.0 else None
    digest["budget"] = {
        "ticks": ticks,
        "est_tokens": est,
        "budget_tokens": budget_tokens,
        "pct": pct,
        "basis": "estimate: 3000 tokens/tick overhead + digest_bytes/4; seat-reported usage wins",
        "checkpoint_due": (ticks - last_cp_tick) >= 6
        or pct >= 60.0
        and last_cp_tick < ticks - 2,
        "stop_class": stop_class,
    }
    state.update(
        {
            "fingerprint": fp,
            "ticks": ticks,
            "est_tokens": est,
            "last_tick_at": digest["ts"],
        }
    )
    return digest


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--root",
        default=os.environ.get("LIAISON_ROOT", ""),
        help="continuity root thread id",
    )
    p.add_argument("--register", choices=["attended", "autonomous"], default=None)
    p.add_argument(
        "--budget-tokens",
        type=int,
        default=int(os.environ.get("LIAISON_BUDGET_TOKENS", "700000")),
    )
    p.add_argument("--state-file", default="")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument(
        "--poll", type=int, default=60, help="seconds between bus polls in --loop"
    )
    p.add_argument(
        "--heartbeat",
        type=int,
        default=1800,
        help="max seconds between sentinels in --loop",
    )
    p.add_argument(
        "--mark-checkpoint",
        action="store_true",
        help="record that the seat just checkpointed",
    )
    p.add_argument(
        "--mark-relayed",
        default="",
        help="comma-separated watcher state file names now relayed",
    )
    args = p.parse_args()

    root = str(args.root).strip()
    if not root:
        raise SystemExit("--root (or LIAISON_ROOT) required")
    state_path = (
        Path(args.state_file)
        if args.state_file
        else _WATCH_DIR / f"liaison-{root}.tick.json"
    )
    state = _load_state(state_path)
    register = args.register or str(state.get("register") or "attended")
    state["register"] = register
    state.setdefault("born_epoch", time.time())
    state.setdefault("born_at", _utcnow())

    if args.mark_checkpoint:
        state["last_cp_tick"] = int(state.get("ticks") or 0)
    if args.mark_relayed:
        relayed = set(state.get("relayed_watchers") or [])
        relayed.update(x.strip() for x in args.mark_relayed.split(",") if x.strip())
        state["relayed_watchers"] = sorted(relayed)
    if args.mark_checkpoint or args.mark_relayed:
        _save_state(state_path, state)
        if not (args.once or args.loop):
            print(json.dumps({"ok": True, "state": str(state_path)}))
            return 0

    if not args.loop:
        digest = build_digest(
            root, state, register=register, budget_tokens=args.budget_tokens
        )
        _save_state(state_path, state)
        print(json.dumps(digest, default=str))
        return 0

    last_emit = 0.0
    print(
        json.dumps(
            {
                "loop": "armed",
                "root": root,
                "poll_s": args.poll,
                "heartbeat_s": args.heartbeat,
            }
        ),
        flush=True,
    )
    while True:
        try:
            digest = build_digest(
                root, state, register=register, budget_tokens=args.budget_tokens
            )
        except (httpx.HTTPError, OSError) as exc:  # transport blip: retry next poll
            print(
                json.dumps({"loop": "transport_error", "error": str(exc)[:200]}),
                flush=True,
            )
            time.sleep(args.poll)
            continue
        now = time.monotonic()
        due = (
            digest["changed_since_last_tick"]
            or (now - last_emit) >= args.heartbeat
            or digest["budget"]["stop_class"]
        )
        if due:
            _save_state(state_path, state)
            print(f"{_SENTINEL} {json.dumps(digest, default=str)}", flush=True)
            last_emit = now
        else:
            # Not emitted: roll the counters back so unemitted polls cost no budget.
            state["ticks"] = int(state["ticks"]) - 1
            state["est_tokens"] = (
                int(state["est_tokens"])
                - _TICK_OVERHEAD_TOKENS
                - len(json.dumps(digest)) // 4
            )
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
