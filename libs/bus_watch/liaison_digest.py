"""Liaison digest — one lean, read-only bus/fleet snapshot per wake.

Assembles what a continuity-root liaison seat needs to decide a tick without
reading any thread linearly: root counters, child + admit-linked worker lanes,
terminal-class subjects, the unread TOC scoped to those lanes, completed
watcher state files not yet relayed, fleet health, the single-Fable lock, and a
context-budget governor whose numbers carry their basis (an estimate — the
seat's own usage reading wins).

Bus access mirrors scripts/watch-cursor-bridge-inbox.py (UDS + AGENT_BUS_TOKEN).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from bus_watch.fable_lock import MAX_HOPS_PER_NIGHT, WATCH_DIR, read_lock

_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_STARGATE_HEALTH = os.environ.get(
    "LIAISON_STARGATE_HEALTH", "http://localhost:9999/health"
)
_GIW_HEALTH = os.environ.get("LIAISON_GIW_HEALTH", "http://127.0.0.1:8091/health")
_WATCH_DIR = WATCH_DIR
_TERMINAL_RE = re.compile(
    r"CLOSEOUT|status:done|status:failed|status:needs-attended|SCORE_RESURFACE|"
    r"stall-pop|PARKED|FAILED|CHECKPOINT|BRIDGE_ACK",
    re.I,
)
# Per-tick fixed overhead the seat spends reading the digest and deciding.
TICK_OVERHEAD_TOKENS = 3000
_TICK_OVERHEAD_TOKENS = TICK_OVERHEAD_TOKENS
_MAX_LANES = 25
_SUBJECT_CAP = 120
_WORKER_RE = re.compile(r"Worker thread `(\d+)`")
_MAX_HOPS_PER_NIGHT = MAX_HOPS_PER_NIGHT


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


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
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
        "fable_lock": read_lock(),
        "hop_cap": {"max_hops_per_night": _MAX_HOPS_PER_NIGHT},
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


__all__ = ["TICK_OVERHEAD_TOKENS", "build_digest", "load_state", "save_state"]
