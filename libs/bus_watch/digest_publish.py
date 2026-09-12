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

_BODY_CAP = 4096
_LANE_CAP = 12


def project_digest(digest: dict[str, Any]) -> dict[str, Any]:
    """Drop unlisted digest keys; keep lanes with unread or non-terminal status."""
    lanes_in = digest.get("lanes") or []
    filtered = [
        lane
        for lane in lanes_in
        if (lane.get("unread") or 0) > 0 or not lane.get("terminal")
    ]
    lanes, omitted = filtered[:_LANE_CAP], max(0, len(filtered) - _LANE_CAP)
    root = digest.get("root") or {}
    policy = digest.get("policy") or {}
    budget = digest.get("budget") or {}
    out: dict[str, Any] = {
        "ts": digest.get("ts"),
        "root": {
            "id": root.get("id"),
            "slug": root.get("slug"),
            "turns": root.get("turns"),
            "unread": root.get("unread"),
            "last_subject": root.get("last_subject"),
        },
        "attention": digest.get("attention"),
        "checkpoint_due": digest.get("checkpoint_due"),
        "lanes": lanes,
        "watchers": digest.get("watchers_complete_unrelayed"),
        "policy": {
            "gear": policy.get("gear"),
            "successor_model": policy.get("successor_model"),
            "post_digest": policy.get("post_digest"),
        },
        "budget": {"stop_class": budget.get("stop_class")},
    }
    if omitted:
        out["lanes_omitted"] = omitted
    return out


def render_body(projection: dict[str, Any], *, cap: int = _BODY_CAP) -> str | None:
    """Compact JSON body; shrink ``lanes`` until the UTF-8 byte length fits ``cap``."""
    all_lanes = list(projection.get("lanes") or [])
    base_omitted = int(projection.get("lanes_omitted") or 0)
    n = len(all_lanes)
    while True:
        proj = dict(projection)
        proj["lanes"] = all_lanes[:n]
        extra = len(all_lanes) - n
        if base_omitted + extra:
            proj["lanes_omitted"] = base_omitted + extra
        elif "lanes_omitted" in proj:
            del proj["lanes_omitted"]
        body = json.dumps(proj, separators=(",", ":"), default=str)
        if len(body.encode("utf-8")) <= cap:
            return body
        if n == 0:
            return None
        n -= 1


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
