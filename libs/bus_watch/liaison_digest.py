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

import json
import os
import re
from typing import Any

import httpx

from bus_watch.digest_budget import (
    _BUDGET_SCOPE,
    GEAR_PRESETS,
    POLICY_DEFAULTS,
    _bus,
    _get,
    _holder_dispatch_id,
    _read_sdk_usage_live,
    _utcnow,
    build_budget_block,
    digest_fingerprint,
    effective_policy,
    health_probe,
)
from bus_watch.fable_lock import WATCH_DIR, current_night_id, read_lock
from bus_watch.life_digest import build_life_block, project_life_block

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
_health = health_probe
_fingerprint = digest_fingerprint


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


def is_life_root(root: dict[str, Any]) -> bool:
    tags = root.get("tags")
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    return isinstance(tags, list) and "lane:life" in tags


def build_digest(
    root_id: str, state: dict[str, Any], *, register: str, budget_tokens: int
) -> dict[str, Any]:
    """Assemble one digest; mutates ``state`` counters (ticks, est_tokens)."""
    with _bus() as client:
        root = _get(client, f"/threads/{root_id}") or {}
        lanes = _child_lanes(client, root_id) if "_error" not in root else []
        lane_ids = {root_id, *(lane["id"] for lane in lanes)}
        unread = _unread_toc(client, lane_ids)

    fp = digest_fingerprint(root, lanes)
    changed = fp != state.get("fingerprint")
    ticks = int(state.get("ticks") or 0) + 1
    policy = effective_policy(state)
    night_id = current_night_id()
    digest_ts = _utcnow()
    lock_now = read_lock()
    dispatches = int(
        (state.get("dispatches_tonight_by_night") or {}).get(night_id)
        or state.get("dispatches_tonight")
        or 0
    )
    digest: dict[str, Any] = {
        "ts": digest_ts,
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
        "attention": [lane for lane in lanes if (lane["unread"] or 0) > 0],
        "unread_toc": unread,
        "watchers_complete_unrelayed": _watchers(state, lane_ids),
        "fleet": {
            "stargate": health_probe(_STARGATE_HEALTH),
            "giw": health_probe(_GIW_HEALTH),
        },
        "fable_lock": lock_now,
        # A gear preset or --set override raises the cap; this field must move
        # with it or a seat stops at 8 while policy authorises 16 (row R14).
        # lock.hops is fleet-wide (one liaison-fable.lock, all roots). Seats must
        # not treat lock_hops == 8 as this root's designed stop (10534 2026-09-12).
        "hop_cap": {
            "max_hops_per_night": policy["max_hops_per_night"],
            "source": "policy.max_hops_per_night",
            "lock_hops": int(lock_now.get("hops") or 0),
            "lock_hops_scope": "fleet",
            "lock_night_id": lock_now.get("night_id"),
        },
        "policy": policy,
        "dispatches_tonight": {
            "value": dispatches,
            "source": "state.dispatches_tonight_by_night",
            "as_of": digest_ts,
        },
        "night_id": night_id,
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
    model = str(policy.get("successor_model") or "")
    lock = digest.get("fable_lock") or read_lock()
    holder_dispatch = _holder_dispatch_id(str(lock.get("holder") or ""))
    usage_live = _read_sdk_usage_live(holder_dispatch) if holder_dispatch else None
    if usage_live:
        used_tokens = int(usage_live.get("used_tokens") or 0)
        budget = build_budget_block(
            used_tokens=used_tokens,
            window_limit_tokens=int(
                usage_live.get("window_limit_tokens") or budget_tokens
            ),
            model=str(usage_live.get("model") or model),
            source="giw.sdk_stream",
            scope=_BUDGET_SCOPE,
            epoch=str(usage_live.get("epoch") or holder_dispatch or ""),
            as_of=str(usage_live.get("as_of") or digest_ts),
        )
    else:
        budget = build_budget_block(
            used_tokens=est,
            window_limit_tokens=budget_tokens,
            model=model,
            source="digest.estimate",
            scope=_BUDGET_SCOPE,
            epoch=str(state.get("budget_epoch") or fp),
            as_of=digest_ts,
        )
        attention = [
            item
            for item in (digest.get("attention") or [])
            if item.get("kind") != "budget_estimate"
        ]
        attention.append(
            {
                "kind": "budget_estimate",
                "used_tokens": est,
                "window_limit_tokens": budget_tokens,
                "pct": pct,
            }
        )
        digest["attention"] = attention
    digest["budget"] = budget
    if is_life_root(root):
        digest["life"] = project_life_block(
            build_life_block(
                goals=[],
                consents=[],
                facts={"dispatches_today": dispatches},
                policy=policy,
                as_of=digest_ts,
            )
        )
    digest["checkpoint_due"] = (ticks - last_cp_tick) >= 6 or (
        pct >= 60.0 and last_cp_tick < ticks - 2
    )
    digest["summary_row"] = state.get("summary_row")
    state.update(
        {
            "fingerprint": fp,
            "ticks": ticks,
            "est_tokens": est,
            "last_tick_at": digest["ts"],
        }
    )
    return digest


__all__ = [
    "GEAR_PRESETS",
    "POLICY_DEFAULTS",
    "TICK_OVERHEAD_TOKENS",
    "build_digest",
    "effective_policy",
    "is_life_root",
]
