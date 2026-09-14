"""Publish a compact DIGEST turn on the continuity root (observe-by-push, R13).

The host tick loop posts one superseding bus turn per digest change so a woken
liaison seat can read the house via ``agent_bus_read(fetch, last=3, compact=true)``
without linear thread reads. Body is projected JSON capped at 4 KB.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from bus_watch.liaison_digest import _bus, effective_policy
from bus_watch.spawn_pending import row_is_terminal

_BODY_CAP = 4096
_LANE_CAP = 12


def _lane_rank(row: dict[str, Any]) -> int:
    if row.get("nag"):
        return 3
    unread = (row.get("unread") or 0) > 0
    if row_is_terminal(row):
        return 2 if unread else 1
    return 0 if unread else 1


def _attn_rank(item: dict[str, Any]) -> int:
    if item.get("kind") in ("friction", "budget_estimate"):
        return -1
    lc = str(item.get("lifecycle") or "").lower()
    if lc in ("abandoned", "failed"):
        return 2
    if row_is_terminal(item):
        return 1
    return 0


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
        },
        "attention": attention,
        "checkpoint_due": digest.get("checkpoint_due"),
        "lanes": lanes,
        "watchers": digest.get("watchers_complete_unrelayed"),
        "policy": {
            "gear": policy.get("gear"),
            "successor_model": policy.get("successor_model"),
            "successor_model_source": policy.get("successor_model_source"),
            "post_digest": policy.get("post_digest"),
            # Standing operator binds (liaison-tick --set induction_binds=…); the
            # navigator reads the published DIGEST policy object, not the full
            # state file — omitting this key made writes silently decorative (a:33719).
            "induction_binds": policy.get("induction_binds"),
        },
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


def _publish_failed(error: str) -> None:
    print(
        json.dumps({"loop": "digest_publish_failed", "error": error[:200]}),
        flush=True,
    )


def publish_digest(
    root_id: str,
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any] | None:
    """Post ``DIGEST <root> <ts>`` on the root; supersede the prior digest turn."""
    body = render_body(project_digest(digest))
    if body is None:
        _publish_failed("body_exceeds_cap")
        return None
    ts = digest.get("ts") or ""
    payload: dict[str, Any] = {
        "thread": root_id,
        "to": "web-anthropic",
        "from": "cursor",
        "subject": f"DIGEST {root_id} {ts}",
        "body": body,
    }
    prior = state.get("digest_turn_number")
    if prior is not None:
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
        _publish_failed(f"{type(exc).__name__}: {exc}")
        return None
    if resp.status_code >= 400:
        _publish_failed(f"http_{resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        _publish_failed("non_json_response")
        return None
    turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
    turn_number = turn.get("turn_number")
    turn_id = turn.get("id")
    if turn_number is None:
        _publish_failed("missing_turn_number")
        return None
    state["digest_turn_number"] = turn_number
    if turn_id is not None:
        state["digest_turn_id"] = turn_id
    return {"turn_number": turn_number, "turn_id": turn_id}


def publish_if_enabled(
    root_id: str,
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    require_change: bool = False,
    client: httpx.Client | None = None,
) -> bool:
    """Publish when ``post_digest`` policy is on; optional digest-change gate.

    Our own DIGEST turn bumps the root's turn count, so the next tick would read as
    "changed" and publish again every poll. ``is_own_digest_echo`` recognises that echo:
    the root's newest turn is the digest we posted and the lanes/attention we
    published from are unchanged — nothing happened, so nothing is published.
    """
    if require_change and not digest.get("changed_since_last_tick"):
        return False
    if not effective_policy(state).get("post_digest"):
        return False
    if is_own_digest_echo(digest, state):
        return False
    published = publish_digest(root_id, digest, state, client=client)
    if published is not None:
        state["digest_lanes_fp"] = _lanes_fingerprint(digest)
    return published is not None


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
    "is_own_digest_echo",
    "project_digest",
    "publish_digest",
    "publish_if_enabled",
    "render_body",
]
