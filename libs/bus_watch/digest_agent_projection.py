"""Agent-facing liaison digest projection — bounded, deduplicated, retrievable full state.

``build_digest`` keeps the authoritative machine snapshot; this module projects what
liaison seats read on tick without duplicating lane bodies under attention or
re-pouring closed lane logs.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from bus_watch.digest_publish import project_digest
from bus_watch.spawn_pending import row_is_terminal

DEFAULT_AGENT_DIGEST_CAP_BYTES = int(
    os.environ.get("LIAISON_DIGEST_AGENT_CAP_BYTES", "32768")
)
_SUBJECT_CAP = 120
_BODY_PREVIEW = 240


def _turn_identity(row: dict[str, Any]) -> str:
    tn = row.get("turn_number")
    frm = row.get("from") or row.get("from_agent") or ""
    subj = str(row.get("subject") or "")[:_SUBJECT_CAP]
    return f"{tn}:{frm}:{subj}"


def _compact_turn_row(row: dict[str, Any], *, body_cap: int = _BODY_PREVIEW) -> dict[str, Any]:
    body = str(row.get("body") or "")
    truncated = len(body) > body_cap
    if truncated:
        body = body[:body_cap]
    out: dict[str, Any] = {
        "turn_number": row.get("turn_number"),
        "from": row.get("from") or row.get("from_agent"),
        "subject": str(row.get("subject") or "")[:_SUBJECT_CAP],
    }
    if body:
        out["body"] = body
    if truncated:
        out["body_truncated"] = True
        out["body_retrieval"] = (
            f"agent_bus_read(get, thread=<root>, turn={row.get('turn_number')})"
        )
    return out


def _lane_resolved(lane: dict[str, Any]) -> bool:
    if (lane.get("unread") or 0) > 0:
        return False
    lc = str(lane.get("lifecycle") or "").lower()
    if lc in ("failed", "abandoned"):
        return False
    if row_is_terminal(lane) and lc in ("completed", "closed", ""):
        return True
    return bool(row_is_terminal(lane)) and str(lane.get("status") or "").lower() == "closed"


def _compact_lane_disposition(lane: dict[str, Any]) -> dict[str, Any]:
    lid = str(lane.get("id") or "")
    lc = str(lane.get("lifecycle") or lane.get("status") or "")
    return {
        "id": lid,
        "slug": lane.get("slug"),
        "disposition": "closed_summary",
        "lifecycle": lc,
        "terminal": bool(lane.get("terminal")),
        "last_subject": str(lane.get("last_subject") or "")[:_SUBJECT_CAP],
        "updated_at": lane.get("updated_at"),
        "retrieval": f"agent_bus_read(thread_get, thread={lid}, last=5, compact=true)",
    }


def _compact_lane_actionable(lane: dict[str, Any]) -> dict[str, Any]:
    row = {
        k: lane[k]
        for k in (
            "id",
            "slug",
            "status",
            "lifecycle",
            "lane_role",
            "turns",
            "unread",
            "last_from",
            "last_subject",
            "terminal",
            "nag",
            "updated_at",
            "execution_id",
            "dispatch_id",
            "contract",
        )
        if k in lane
    }
    unread_turns = lane.get("unread_turns")
    if isinstance(unread_turns, list) and unread_turns:
        row["unread_turns"] = [
            _compact_turn_row(t) for t in unread_turns if isinstance(t, dict)
        ]
    return row


def _project_lanes(lanes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_id: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lid = str(lane.get("id") or "")
        if not lid:
            continue
        if _lane_resolved(lane):
            compact = _compact_lane_disposition(lane)
        else:
            compact = _compact_lane_actionable(lane)
        by_id[lid] = compact
        out.append(compact)
    return out, {lid: idx for idx, lid in enumerate(by_id)}


def _slim_attention_item(
    item: dict[str, Any],
    *,
    lane_index: dict[str, int],
    lanes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    kind = item.get("kind")
    if kind and kind != "lane":
        slim = {k: item[k] for k in item if k != "unread_turns"}
        lid = str(item.get("id") or "")
        if lid and lid in lane_index:
            slim["lane_ref"] = f"lanes[{lane_index[lid]}]"
            for drop in ("last_subject", "slug", "turns", "last_from", "terminal"):
                slim.pop(drop, None)
        return slim
    lid = str(item.get("id") or "")
    if lid and lid in lane_index:
        return {
            "kind": "lane",
            "id": lid,
            "lane_ref": f"lanes[{lane_index[lid]}]",
            "unread": item.get("unread"),
            "lifecycle": item.get("lifecycle"),
        }
    slim = dict(item)
    unread_turns = slim.pop("unread_turns", None)
    if isinstance(unread_turns, list) and unread_turns:
        slim["unread_turns"] = [
            _compact_turn_row(t) for t in unread_turns if isinstance(t, dict)
        ]
    return slim


def _dedupe_root_notices(
    root: dict[str, Any],
    attention: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    seen: set[str] = set()
    for item in attention:
        if not isinstance(item, dict):
            continue
        for row in item.get("unread_turns") or []:
            if isinstance(row, dict):
                seen.add(_turn_identity(row))
    root_out = dict(root)
    for key in ("recent_turns", "unread_turns"):
        rows = root_out.get(key)
        if not isinstance(rows, list):
            continue
        compact: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident = _turn_identity(row)
            if ident in seen:
                continue
            compact.append(_compact_turn_row(row))
        root_out[key] = compact
    return root_out, seen


def project_liaison_digest_agent(
    digest: dict[str, Any],
    *,
    cap_bytes: int = DEFAULT_AGENT_DIGEST_CAP_BYTES,
) -> dict[str, Any]:
    """Compact digest for agent-facing CLI/tool responses; full digest stays internal."""
    base = project_digest(digest)
    lanes_in = list(base.get("lanes") or digest.get("lanes") or [])
    lanes, lane_index = _project_lanes(lanes_in)
    attention_in = list(base.get("attention") or [])
    attention = [
        _slim_attention_item(item, lane_index=lane_index, lanes=lanes)
        for item in attention_in
        if isinstance(item, dict)
    ]
    root, _seen = _dedupe_root_notices(dict(base.get("root") or {}), attention)
    fp_src = json.dumps(digest, sort_keys=True, default=str).encode("utf-8")
    out: dict[str, Any] = {
        **base,
        "root": root,
        "lanes": lanes,
        "attention": attention,
        "projection": {
            "source": "digest_agent_projection",
            "full_digest_sha256": hashlib.sha256(fp_src).hexdigest(),
            "cap_bytes": cap_bytes,
        },
    }
    for drop in ("unread_toc",):
        out.pop(drop, None)
    while len(json.dumps(out, separators=(",", ":"), default=str).encode("utf-8")) > cap_bytes:
        if len(out.get("lanes") or []) > 0:
            out["lanes"] = out["lanes"][:-1]
            out.setdefault("lanes_omitted", 0)
            out["lanes_omitted"] = int(out["lanes_omitted"]) + 1
            continue
        attn = list(out.get("attention") or [])
        protected = sum(
            1 for item in attn if isinstance(item, dict) and item.get("kind") in ("friction", "budget_estimate")
        )
        if len(attn) > protected:
            out["attention"] = attn[:-1]
            out.setdefault("attention_omitted", 0)
            out["attention_omitted"] = int(out["attention_omitted"]) + 1
            continue
        out["overflow"] = {
            "reason": "mandatory_exceeds_cap",
            "retrieval": "liaison-tick.py --root <id> --full-digest (internal)",
        }
        break
    return out


def render_liaison_digest_agent_json(
    digest: dict[str, Any],
    *,
    cap_bytes: int = DEFAULT_AGENT_DIGEST_CAP_BYTES,
) -> str:
    proj = project_liaison_digest_agent(digest, cap_bytes=cap_bytes)
    return json.dumps(proj, default=str)


__all__ = [
    "DEFAULT_AGENT_DIGEST_CAP_BYTES",
    "project_liaison_digest_agent",
    "render_liaison_digest_agent_json",
    "_compact_turn_row",
    "_lane_resolved",
]
