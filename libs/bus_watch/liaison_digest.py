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

from bus_watch.fable_lock import (
    MAX_HOPS_PER_NIGHT,
    WATCH_DIR,
    current_night_id,
    read_lock,
)

_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_STARGATE_HEALTH = os.environ.get(
    "LIAISON_STARGATE_HEALTH", "http://localhost:9999/health"
)
_GIW_HEALTH = os.environ.get("LIAISON_GIW_HEALTH", "http://127.0.0.1:8091/health")
_GIW_USAGE_LIVE = os.environ.get(
    "LIAISON_GIW_USAGE_LIVE", "http://127.0.0.1:8091/api/v1/cursor/dispatch-usage-live"
)
_BUDGET_SCOPE = "liaison_seat"
_BUDGET_RATIO_THRESHOLD = 0.80
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


# Economy gears (operator 2026-09-10): the Fable 1M Max spend window holds through
# the MVP and maybe one iteration; the successor model is policy, never a constant.
# Gear 1 = tonight's MVP. Shift with ``liaison-tick.py --set successor_model=…``.
POLICY_DEFAULTS: dict[str, Any] = {
    "gear": "1-fable-mvp",
    "successor_model": "cursor/claude-fable-5-1",
    "successor_cost_intent": "deliberate_high_cost",
    "max_ticks_per_hop": 5,
    "max_hop_minutes": 60,
    "poll_seconds": 600,
    "max_hops_per_night": MAX_HOPS_PER_NIGHT,
    "max_dispatches_per_night": 12,
    "wake_on_attention_only": False,
    "spawn_grace_seconds": 900,
    "successor_seat": "cursor-sdk",
    "budget_max_age_s": 300,
    # Attended IDE hop target: the graphical host whose focused Cursor window is the
    # operator's. None ⇒ `liaison-ide-hop.py` refuses to fire — a wrong default put
    # hop 1/2 on an unattended window (2026-09-11 18:20 PT); set with
    # `--set gui_host=<ssh host>`.
    "gui_host": None,
    # Gear 3 is disarmed until the operator sets `--set ready=true` for a first live
    # night (A7 gate). A True default would let any `--loop --spawn-on-wake` start
    # spawn premium successors on the first digest with attention.
    "ready": False,
}
GEAR_PRESETS: dict[str, dict[str, Any]] = {
    "1-fable-mvp": {},
    "2-opus-hops": {
        "successor_model": "cursor/claude-opus-5",
        "successor_cost_intent": None,
        "max_ticks_per_hop": 6,
    },
    "3-wake-on-attention": {
        "successor_model": "cursor/claude-opus-5",
        "successor_cost_intent": None,
        "wake_on_attention_only": True,
        "poll_seconds": 120,
        "spawn_grace_seconds": 900,
    },
    # claude.ai seat (operator 2026-09-10: expanded usage there; Fable exhausted
    # until Sunday → opus). Successor is a CDP mission session on /mcp/life.
    # NOT READY until the life-surface successor packet lands (todo
    # cse-liaison-seat-claude-ai): the spawn call shape differs (team_dispatch
    # model=cdp/…, purpose=mission) and the seat has no shell for the tick script.
    "4-cdp-liaison": {
        "successor_seat": "cdp",
        "successor_model": "cdp/opus-5",
        "successor_cost_intent": None,
        "ready": False,
    },
}


def _holder_dispatch_id(holder: str | None) -> str | None:
    text = str(holder or "")
    if text.startswith("sdk:"):
        return text.split(":", 1)[1] or None
    return None


def _read_sdk_usage_live(dispatch_id: str) -> dict[str, Any] | None:
    """Fetch live stream usage for the holder dispatch from GIW."""
    try:
        response = httpx.get(
            _GIW_USAGE_LIVE,
            params={"dispatch_id": dispatch_id},
            timeout=3.0,
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    usage_live = payload.get("usage_live")
    if not isinstance(usage_live, dict):
        return None
    return usage_live


def build_budget_block(
    *,
    used_tokens: int,
    window_limit_tokens: int,
    model: str,
    source: str,
    scope: str,
    epoch: str,
    as_of: str,
) -> dict[str, Any]:
    """Status-basis budget envelope for the liaison digest."""
    ratio = used_tokens / max(window_limit_tokens, 1)
    stop_class = (
        "CONTEXT_BUDGET"
        if source == "giw.sdk_stream" and ratio >= _BUDGET_RATIO_THRESHOLD
        else None
    )
    return {
        "used_tokens": used_tokens,
        "window_limit_tokens": window_limit_tokens,
        "model": model,
        "as_of": as_of,
        "source": source,
        "scope": scope,
        "epoch": epoch,
        "stop_class": stop_class,
    }


def effective_policy(state: dict[str, Any]) -> dict[str, Any]:
    """Defaults ← gear preset ← explicit ``policy`` overrides stored in state."""
    overrides = dict(state.get("policy") or {})
    gear = str(overrides.get("gear") or POLICY_DEFAULTS["gear"])
    merged = {
        **POLICY_DEFAULTS,
        **GEAR_PRESETS.get(gear, {}),
        **overrides,
        "gear": gear,
    }
    merged["successor_is_fable"] = "fable" in str(merged.get("successor_model") or "")
    if gear == "3-wake-on-attention" and "ready" not in overrides:
        merged["ready"] = False
    return merged


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
    policy = effective_policy(state)
    night_id = current_night_id()
    digest_ts = _utcnow()
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
        "fleet": {"stargate": _health(_STARGATE_HEALTH), "giw": _health(_GIW_HEALTH)},
        "fable_lock": read_lock(),
        # A gear preset or --set override raises the cap; this field must move
        # with it or a seat stops at 8 while policy authorises 16 (row R14).
        "hop_cap": {
            "max_hops_per_night": policy["max_hops_per_night"],
            "source": "policy.max_hops_per_night",
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
    "build_budget_block",
    "build_digest",
    "effective_policy",
    "load_state",
    "save_state",
]
