"""Liaison digest — one lean, read-only bus/fleet snapshot per wake.

Assembles what a continuity-root liaison seat needs to decide a tick without
reading any thread linearly: root counters, child + admit-linked worker lanes,
terminal-class subjects, the unread TOC scoped to those lanes, completed
watcher state files not yet relayed, open frictions on the charter-owned
services (``bus_watch.friction_rows``), fleet health, the single-Fable lock, and
a context-budget governor whose numbers carry their basis (an estimate — the
seat's own usage reading wins).

Bus access mirrors scripts/watch-cursor-bridge-inbox.py (UDS + AGENT_BUS_TOKEN).
"""

from __future__ import annotations

import json
import os
from typing import Any

from bus_watch.digest_budget import (
    GEAR_PRESETS,
    POLICY_DEFAULTS,
    _bus,
    _get,
    _holder_dispatch_id,
    _read_sdk_usage_live,
    _utcnow,
    digest_fingerprint,
    effective_policy,
    health_probe,
    seat_budget,
)
from bus_watch.digest_lanes import (  # noqa: F401
    _child_lanes,
    _lane_row,
    _lineage_lanes,
    _merge_observe_lanes,
    _unread_toc,
)
from bus_watch.digest_unread import DIGEST_UNREAD_TO, root_unread_key
from bus_watch.events import emit_checkpoint_observed
from bus_watch.fable_lock import WATCH_DIR, current_night_id, read_lock
from bus_watch.friction_rows import fold_fingerprint, harvest_frictions
from bus_watch.ide_budget import measure_ide_tab
from bus_watch.induction import build_wake_induction
from bus_watch.liaison_watchers import collect_watchers
from bus_watch.life_digest import build_life_block, project_life_block
from bus_watch.loop_tape import loop_tape_thread
from bus_watch.now_row import harvest_policy_entity_cache
from bus_watch.now_row_bind import ticker_owns_bind
from bus_watch.quiet_reason import usable_holder_id
from bus_watch.roster import fold_roster
from bus_watch.spawn_pending import (
    build_attention_lanes,
    digest_root_surface,
    observe_terminal_lane_closeouts,
)
from bus_watch.spawn_wake.review_apply import ready_review_apply_attention
from bus_watch.spawn_wake.row_class import ready_row_class_attention

_STARGATE_HEALTH = os.environ.get(
    "LIAISON_STARGATE_HEALTH", "http://localhost:9999/health"
)
_GIW_HEALTH = os.environ.get("LIAISON_GIW_HEALTH", "http://127.0.0.1:8091/health")
_WATCH_DIR = WATCH_DIR
TICK_OVERHEAD_TOKENS = 3000
_SUBJECT_CAP = 120
_health = health_probe
_fingerprint = digest_fingerprint
# Module alias so hermetic tests can patch the tab lookup (it reads ~/.cursor).
_measure_ide_tab = measure_ide_tab


def apply_projection_holder_ids(
    lanes: list[Any], projections: list[Any] | None
) -> None:
    """Copy a live projection's holder id onto the lane with the same thread id.

    Does not replace an id the lane already carries. Does not copy a bare
    8-hex prefix.
    """
    by_thread: dict[str, dict[str, Any]] = {}
    for proj in projections or []:
        if not isinstance(proj, dict):
            continue
        thread_id = str(proj.get("thread_id") or "").strip()
        if thread_id:
            by_thread[thread_id] = proj
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        thread_id = str(lane.get("id") or "").strip()
        proj = by_thread.get(thread_id)
        if not proj:
            continue
        lane["live_projection_thread_id"] = thread_id
        dispatch_id = usable_holder_id(proj.get("dispatch_id") or proj.get("op_id"))
        execution_id = usable_holder_id(proj.get("execution_id"))
        if dispatch_id and not usable_holder_id(lane.get("dispatch_id")):
            lane["dispatch_id"] = dispatch_id
        if execution_id and not usable_holder_id(lane.get("execution_id")):
            lane["execution_id"] = execution_id


def is_life_root(root: dict[str, Any]) -> bool:
    tags = root.get("tags")
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    return isinstance(tags, list) and "lane:life" in tags


def build_digest(
    root_id: str, state: dict[str, Any], *, register: str
) -> dict[str, Any]:
    """Assemble one digest; mutates ``state`` counters (ticks, est_tokens)."""
    with _bus() as client:
        root = _get(client, f"/threads/{root_id}", to=DIGEST_UNREAD_TO) or {}
        recent_turns, tip_checkpoint_turn, unread_turns = digest_root_surface(
            client, _get, root_id, root
        )
        lanes = _child_lanes(client, root_id) if "_error" not in root else []
        observe_lanes = (
            _merge_observe_lanes(lanes, _lineage_lanes(client, root_id))
            if "_error" not in root
            else []
        )
        lane_ids = {root_id, *(lane["id"] for lane in lanes)}
        unread = _unread_toc(client, lane_ids)
        attention = build_attention_lanes(
            lanes,
            fetch_unread_turns=lambda tid: (
                _get(client, "/turns", thread=tid, unread=True, last=25) or {}
            ).get("turns"),
        )  # noqa: E501
        observe_terminal_lane_closeouts(
            root_id,
            observe_lanes,
            state,
            client,
            fetch_turns=lambda tid: (
                _get(client, "/turns", thread=tid, last=10) or {}
            ).get("turns"),
        )

    policy = effective_policy(state)
    policy_bind = str(policy.get("now_row") or "").strip()
    entity_cache = harvest_policy_entity_cache(policy_bind) if policy_bind else {}
    night_id = current_night_id()
    frictions = harvest_frictions(state, policy, night_id=night_id)
    tape = loop_tape_thread(root_id, policy)
    fp = fold_fingerprint(
        digest_fingerprint(root, lanes, occupancy_thread=tape), frictions["rows"]
    )
    changed = fp != state.get("fingerprint")
    ticks = int(state.get("ticks") or 0) + 1
    prior_cp_turn = int(state.get("last_cp_turn") or 0)
    if tip_checkpoint_turn is not None and tip_checkpoint_turn > prior_cp_turn:
        state["last_cp_turn"] = tip_checkpoint_turn
        state["last_cp_tick"] = max(int(state.get("last_cp_tick") or 0), ticks)
        emit_checkpoint_observed(
            root=root_id,
            turn=tip_checkpoint_turn,
            prior_turn=prior_cp_turn,
            source="digest.root.tip_checkpoint_turn",
        )
    digest_ts = _utcnow()
    lock_now = read_lock(root_id)
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
            root_unread_key(root): root.get("unread_count"),
            "last_subject": str(root.get("last_subject") or "")[:_SUBJECT_CAP],
            "recent_turns": recent_turns,
            "unread_turns": unread_turns,
            "tip_checkpoint_turn": tip_checkpoint_turn,
            "error": root.get("_error"),
        },
        "now_row_set_at": state.get("now_row_set_at"),
        **(
            {"now_row_bind": state.get("now_row_bind")}
            if ticker_owns_bind(state)
            else {}
        ),
        "policy_entity_cache": entity_cache,
        "register": register,
        "lanes": lanes,
        # fmt: off
        "attention": attention
        + frictions["attention"]
        + ready_row_class_attention(
            {
                "root": {
                    "recent_turns": recent_turns,
                    "unread_turns": unread_turns,
                },
                "frictions": frictions["rows"],
            },
            state,
        )
        + ready_review_apply_attention(
            {
                "root": {
                    "id": root_id,
                    "recent_turns": recent_turns,
                    "unread_turns": unread_turns,
                },
            },
            state,
        ),
        "attention_nag_excluded": {
            "count": sum(1 for lane in lanes if lane.get("nag")),
            "source": "liaison_digest._NAG_RE",
        },
        # fmt: on
        "unread_toc": unread,
        "frictions": frictions["rows"],
        "friction_summary": frictions["summary"],
        "watchers_complete_unrelayed": collect_watchers(
            state, lane_ids, root_id, _WATCH_DIR
        ),
        "fleet": {"stargate": _health(_STARGATE_HEALTH), "giw": _health(_GIW_HEALTH)},
        "fable_lock": lock_now,
        "hop_cap": {
            "max_hops_per_night": policy["max_hops_per_night"],
            "source": "policy.max_hops_per_night",
            "lock_hops": int(lock_now.get("hops") or 0),
            "lock_hops_scope": "root",
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
        + TICK_OVERHEAD_TOKENS
        + len(json.dumps(digest)) // 4
    )
    pct = 0.0
    last_cp_tick = int(state.get("last_cp_tick") or 0)
    model = str(policy.get("successor_model") or "")
    lock = digest.get("fable_lock") or read_lock(root_id)
    holder_dispatch = _holder_dispatch_id(str(lock.get("holder") or ""))
    usage_live = _read_sdk_usage_live(holder_dispatch) if holder_dispatch else None
    budget, pct = seat_budget(
        est=est,
        successor_model=model,
        usage_live=usage_live,
        holder_dispatch=holder_dispatch,
        register=register,
        lock=lock,
        policy=policy,
        root_id=root_id,
        digest_ts=digest_ts,
        epoch=str(state.get("budget_epoch") or fp),
    )
    if budget["source"] != "giw.sdk_stream":
        # fmt: off
        attention = [i for i in (digest.get("attention") or []) if i.get("kind") != "budget_estimate"]
        attention.append({"kind": "budget_estimate", "used_tokens": budget["used_tokens"], "window_limit_tokens": budget["window_limit_tokens"], "pct": pct, "source": budget["source"]})
        # fmt: on
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
    digest["roster"] = fold_roster(root_id, policy, digest)
    digest["induction"] = build_wake_induction(
        digest,
        surface="cse" if register == "cse" else "ide",
        state=state,
        lock=lock_now,
    )
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
