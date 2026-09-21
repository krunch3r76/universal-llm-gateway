"""Publish a compact DIGEST turn on the continuity root (observe-by-push, R13).

The host tick loop posts one superseding bus turn per digest change so a woken
liaison seat can read the house via ``agent_bus_read(fetch, last=3, compact=true)``
without linear thread reads. Body is projected JSON capped at 4 KB.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from typing import Any, Literal

import httpx

from bus_watch.digest_budget import _utcnow
from bus_watch.lane_closeout import lane_status_query_pointer, query_lane_closeouts
from bus_watch.liaison_digest import _bus, effective_policy
from bus_watch.liaison_pager import page_liaison
from bus_watch.loop_tape import loop_tape_thread
from bus_watch.spawn_pending import row_is_terminal

_BODY_CAP = 4096
_LANE_CAP = 12
_STALE_RETRY_MINUTES = 2

PublishOutcome = Literal["published", "skipped", "failed"]


def _lane_rank(row: dict[str, Any]) -> int:
    if row.get("nag"):
        return 3
    unread = (row.get("unread") or 0) > 0
    if row_is_terminal(row):
        return 2 if unread else 1
    return 0 if unread else 1


def _induction_binds_uri(root_id: str) -> str:
    return f"cortex://notes/system/threads/{root_id}-orchestrator/index"


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _digest_is_stale(digest: dict[str, Any], state: dict[str, Any]) -> bool:
    root_turns = (digest.get("root") or {}).get("turns")
    prior = state.get("digest_turn_number")
    if prior is None or root_turns is None:
        return False
    return int(root_turns) > int(prior)


def _stale_retry_due(state: dict[str, Any], *, now: float | None = None) -> bool:
    now_ts = now if now is not None else time.time()
    last = state.get("last_stale_publish_attempt_at")
    if not last:
        return True
    last_ts = _parse_iso_ts(str(last))
    if last_ts is None:
        return True
    return (now_ts - last_ts) >= (_STALE_RETRY_MINUTES * 60)


def _attn_rank(item: dict[str, Any]) -> int:
    if item.get("kind") in ("friction", "budget_estimate"):
        return -1
    lc = str(item.get("lifecycle") or "").lower()
    if lc in ("abandoned", "failed"):
        return 2
    if row_is_terminal(item):
        return 1
    return 0


def _closeout_journal_meta(root_id: str) -> dict[str, Any]:
    if not root_id:
        return {}
    try:
        with _bus() as client:
            rows = query_lane_closeouts(client, root_id, last=200)
    except Exception:  # noqa: BLE001 — publish must not fail on query errors
        return {}
    if not rows:
        return {}
    return {
        "lane_closeouts_count": len(rows),
        "lane_closeouts_query": lane_status_query_pointer(root_id),
    }


def project_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Drop unlisted digest keys; keep lanes with unread or non-terminal status."""
    lanes_in = digest.get("lanes") or []
    filtered = [
        lane
        for lane in lanes_in
        if (lane.get("unread") or 0) > 0 or not lane.get("terminal")
    ]
    filtered.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    filtered.sort(key=_lane_rank)
    lanes, omitted = filtered[:_LANE_CAP], max(0, len(filtered) - _LANE_CAP)
    attention = list(digest.get("attention") or [])
    attention.sort(key=_attn_rank)
    root = digest.get("root") or {}
    policy = digest.get("policy") or {}
    budget = digest.get("budget") or {}
    root_id = str(root.get("id") or "")
    binds = policy.get("induction_binds") or []
    policy_out: dict[str, Any] = {
        "gear": policy.get("gear"),
        "successor_model": policy.get("successor_model"),
        "successor_model_source": policy.get("successor_model_source"),
        "post_digest": policy.get("post_digest"),
    }
    if binds:
        # Publish floor: count + cortex URI only — full binds live in tick.json.
        policy_out["induction_binds_count"] = len(binds)
        policy_out["induction_binds_uri"] = _induction_binds_uri(root_id)
    closeout_meta = _closeout_journal_meta(root_id)
    if closeout_meta:
        policy_out.update(closeout_meta)
    out: dict[str, Any] = {
        # First key on purpose: the planted address a woken seat reads before
        # the counters (10479 #120 — plant the address in what the seat reads).
        "induction": digest.get("induction"),
        "ts": digest.get("ts"),
        "root": {
            "id": root.get("id"),
            "slug": root.get("slug"),
            "turns": root.get("turns"),
            "unread": root.get("unread"),
            "last_subject": root.get("last_subject"),
            "tip_checkpoint_turn": root.get("tip_checkpoint_turn"),
        },
        "attention": attention,
        "checkpoint_due": digest.get("checkpoint_due"),
        "lanes": lanes,
        "watchers": digest.get("watchers_complete_unrelayed"),
        "policy": policy_out,
        "budget": {
            "stop_class": budget.get("stop_class"),
            "source": budget.get("source"),
            "used_tokens": budget.get("used_tokens"),
            "window_limit_tokens": budget.get("window_limit_tokens"),
        },
    }
    if omitted:
        out["lanes_omitted"] = omitted
    life = digest.get("life")
    if isinstance(life, dict):
        out["life"] = life
    return out


def render_body(projection: dict[str, Any], *, cap: int = _BODY_CAP) -> str | None:
    """Compact JSON body; shrink by class until UTF-8 length fits ``cap``.

    Returns ``None`` only when the floor (induction, root, protected attention
    kinds, omitted counts, policy, budget) alone exceeds ``cap`` — 27 full lane
    attention rows once froze publish at ``_BODY_CAP`` (a:33433).
    """
    lanes = list(projection.get("lanes") or [])
    attention = list(projection.get("attention") or [])
    watchers = list(projection.get("watchers") or [])
    base_lanes_omitted = int(projection.get("lanes_omitted") or 0)
    protected_attn = sum(1 for item in attention if _attn_rank(item) < 0)
    n_lanes, n_attn = len(lanes), len(attention)
    drop_life = drop_watchers = False

    def _assemble() -> str:
        proj = dict(projection)
        proj["lanes"] = lanes[:n_lanes]
        proj["attention"] = attention[:n_attn]
        lanes_omitted = base_lanes_omitted + len(lanes) - n_lanes
        attn_omitted = len(attention) - n_attn
        if lanes_omitted:
            proj["lanes_omitted"] = lanes_omitted
        elif "lanes_omitted" in proj:
            del proj["lanes_omitted"]
        if attn_omitted:
            proj["attention_omitted"] = attn_omitted
        elif "attention_omitted" in proj:
            del proj["attention_omitted"]
        if drop_life:
            proj.pop("life", None)
        if drop_watchers:
            proj.pop("watchers", None)
            if watchers:
                proj["watchers_omitted"] = len(watchers)
        proj["caps"] = {"body_bytes": cap, "source": "digest_publish._BODY_CAP"}
        return json.dumps(proj, separators=(",", ":"), default=str)

    while True:
        body = _assemble()
        if len(body.encode("utf-8")) <= cap:
            return body
        if n_lanes > 0:
            n_lanes -= 1
            continue
        if n_attn > protected_attn:
            n_attn -= 1
            continue
        if not drop_life and isinstance(projection.get("life"), dict):
            drop_life = True
            continue
        if not drop_watchers and watchers:
            drop_watchers = True
            continue
        return None


def _publish_skipped(reason: str) -> None:
    print(
        json.dumps({"loop": "digest_publish_skipped", "reason": reason}),
        flush=True,
    )


def _publish_failed(root_id: str, state: dict[str, Any], error: str) -> None:
    err = error[:200]
    state["last_publish_error"] = err
    state["last_publish_attempt_at"] = _utcnow()
    print(json.dumps({"loop": "digest_publish_failed", "error": err}), flush=True)
    print(f"DIGEST_PUBLISH_FAILED root={root_id} error={err}", file=sys.stderr, flush=True)
    if state.get("last_paged_publish_error") != err:
        page_liaison(
            root_id,
            f"liaison {root_id} — digest publish failed",
            err,
        )
        state["last_paged_publish_error"] = err


def publish_digest(
    root_id: str,
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Post ``DIGEST <root> <ts>`` on the occupancy thread; supersede prior digest."""
    body = render_body(project_digest(digest))
    if body is None:
        _publish_failed(root_id, state, "body_exceeds_cap")
        return None
    ts = digest.get("ts") or ""
    tape = loop_tape_thread(root_id, effective_policy(state), digest.get("policy"))
    payload: dict[str, Any] = {
        "thread": tape,
        "to": "web-anthropic",
        "from": "cursor",
        "subject": f"DIGEST {root_id} {ts}",
        "body": body,
    }
    prior = state.get("digest_turn_number")
    prior_thread = str(state.get("digest_publish_thread") or root_id)
    # Turn numbers are per-thread. A 10479 DIGEST turn cannot supersede on 11876
    # (http_422 on the first Phase-2 post, 2026-09-20T16:41Z).
    if prior is not None and prior_thread == tape:
        payload["supersedes_turn"] = prior

    def _post(c: httpx.Client) -> httpx.Response:
        return c.post("/threads/send", json=payload)

    try:
        if client is not None:
            resp = _post(client)
        else:
            with _bus() as c:
                resp = _post(c)
    except httpx.HTTPError as exc:
        _publish_failed(root_id, state, f"{type(exc).__name__}: {exc}")
        return None
    if resp.status_code >= 400:
        _publish_failed(root_id, state, f"http_{resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        _publish_failed(root_id, state, "non_json_response")
        return None
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    turn_number = turn.get("turn_number")
    turn_id = turn.get("id")
    if turn_number is None:
        _publish_failed(root_id, state, "missing_turn_number")
        return None
    state["digest_turn_number"] = turn_number
    state["digest_publish_thread"] = tape
    if turn_id is not None:
        state["digest_turn_id"] = turn_id
    state["last_publish_error"] = None
    state["last_paged_publish_error"] = None
    state["last_publish_attempt_at"] = _utcnow()
    return {"turn_number": turn_number, "turn_id": turn_id}


def publish_if_enabled(
    root_id: str,
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    require_change: bool = False,
    client: httpx.Client | None = None,
) -> PublishOutcome:
    """Publish when ``post_digest`` policy is on; optional digest-change gate.

    Our own DIGEST turn bumps the root's turn count, so the next tick would read as
    "changed" and publish again every poll. ``is_own_digest_echo`` recognises that echo:
    the root's newest turn is the digest we posted and the lanes/attention we
    published from are unchanged — nothing happened, so nothing is published.
    """
    policy = effective_policy(state)
    if not policy.get("post_digest"):
        _publish_skipped("post_digest_off")
        return "skipped"
    loop_thread = str(policy.get("loop_thread") or "").strip()
    if not loop_thread:
        _publish_skipped("loop_thread_unset")
        if str(policy.get("gear") or "") == "3-wake-on-attention":
            ticks = int(state.get("loop_thread_unset_since") or 0) + 1
            state["loop_thread_unset_since"] = ticks
            if ticks >= 4:
                _publish_failed(root_id, state, "loop_thread_unset")
                return "failed"
        return "skipped"
    state.pop("loop_thread_unset_since", None)
    tape = loop_tape_thread(root_id, policy, digest.get("policy"))
    dest_moved = str(state.get("digest_publish_thread") or root_id) != tape
    # own_echo compares root.turns to our last DIGEST turn. After a root-era
    # publish those numbers match, so the first tape post would skip forever.
    if not dest_moved and is_own_digest_echo(digest, state):
        _publish_skipped("own_echo")
        return "skipped"
    changed = bool(digest.get("changed_since_last_tick"))
    # Root turn-count stale heuristic is false once DIGEST leaves the resume root.
    stale = _digest_is_stale(digest, state) if tape == str(root_id) else False
    force_stale = stale and _stale_retry_due(state)
    if require_change and not changed and not force_stale and not dest_moved:
        _publish_skipped("require_change")
        return "skipped"
    if force_stale:
        state["last_stale_publish_attempt_at"] = _utcnow()
    published = publish_digest(root_id, digest, state, client=client)
    if published is not None:
        state["digest_lanes_fp"] = _lanes_fingerprint(digest)
        return "published"
    return "failed"


def _lanes_fingerprint(digest: dict[str, Any]) -> str:
    """Hash of what a reader would act on: lanes, attention, checkpoint_due, watchers.

    Attention items are reduced to their identity (lane id or kind): the budget
    estimate carries token counts that move every tick and would defeat the guard.
    """
    key = {
        "lanes": [
            (
                lane.get("id"),
                lane.get("turns"),
                lane.get("status"),
                lane.get("lifecycle"),
            )
            for lane in (digest.get("lanes") or [])
        ],
        "attention": sorted(
            str(item.get("id") or item.get("kind") or "")
            for item in (digest.get("attention") or [])
            if isinstance(item, dict)
        ),
        "checkpoint_due": digest.get("checkpoint_due"),
        "watchers": digest.get("watchers_complete_unrelayed"),
    }
    return json.dumps(key, sort_keys=True, default=str)


def is_own_digest_echo(digest: dict[str, Any], state: dict[str, Any]) -> bool:
    """True when the only thing that changed since the last tick is our own DIGEST turn.

    Shared by the publisher (no republish) and the attended loop's wake condition
    (no sentinel for our own echo — each publish would otherwise cost the IDE seat
    a wake turn one poll later).
    """
    root_turns = (digest.get("root") or {}).get("turns")
    prior = state.get("digest_turn_number")
    if prior is None or root_turns != prior:
        return False
    return _lanes_fingerprint(digest) == state.get("digest_lanes_fp")


__all__ = [
    "PublishOutcome",
    "is_own_digest_echo",
    "project_digest",
    "publish_digest",
    "publish_if_enabled",
    "render_body",
]
