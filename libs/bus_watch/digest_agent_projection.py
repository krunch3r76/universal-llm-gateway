"""Agent-facing liaison digest projection — bounded, deduplicated, retrievable full state."""

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
_PROTECTED_ATTN_KINDS = frozenset({"friction", "budget_estimate"})


def serialize_agent_digest(payload: dict[str, Any]) -> bytes:
    """Wire format for cap measurement and CLI output."""
    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


def _turn_identity(row: dict[str, Any], *, thread_id: str) -> str:
    tid = str(row.get("thread") or row.get("thread_id") or thread_id)
    tn = row.get("turn_number")
    frm = row.get("from") or row.get("from_agent") or ""
    subj = str(row.get("subject") or "")[:_SUBJECT_CAP]
    return f"{tid}:{tn}:{frm}:{subj}"


def _turn_get_retrieval(thread_id: str, turn_number: Any) -> str:
    return (
        f'agent_bus_read(get, thread={thread_id}, turn={turn_number}, compact=false)'
    )


def _compact_turn_row(
    row: dict[str, Any],
    *,
    thread_id: str,
    body_cap: int = _BODY_PREVIEW,
) -> dict[str, Any]:
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
        out["body_retrieval"] = _turn_get_retrieval(thread_id, row.get("turn_number"))
    return out


def _lane_unresolved_obligation(lane: dict[str, Any]) -> bool:
    lc = str(lane.get("lifecycle") or "").lower()
    if lc in ("failed", "abandoned"):
        return True
    if (lane.get("unread") or 0) > 0:
        return True
    return False


def _lane_resolved(lane: dict[str, Any]) -> bool:
    if _lane_unresolved_obligation(lane):
        return False
    if row_is_terminal(lane) and str(lane.get("status") or "").lower() in {
        "closed",
        "completed",
    }:
        return True
    lc = str(lane.get("lifecycle") or "").lower()
    return bool(row_is_terminal(lane)) and lc in ("completed", "closed", "")


def _lane_retrieval(lid: str) -> str:
    return f"agent_bus_read(thread_get, thread={lid}, last=5, compact=true)"


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
        "retrieval": _lane_retrieval(lid),
    }


def _compact_lane_actionable(lane: dict[str, Any], *, root_id: str) -> dict[str, Any]:
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
    lid = str(lane.get("id") or root_id)
    unread_turns = lane.get("unread_turns")
    if isinstance(unread_turns, list) and unread_turns:
        row["unread_turns"] = [
            _compact_turn_row(t, thread_id=lid) for t in unread_turns if isinstance(t, dict)
        ]
    return row


def _lane_cap_priority(lane: dict[str, Any]) -> int:
    if _lane_unresolved_obligation(lane):
        return 1_000
    if row_is_terminal(lane):
        return 200
    return 500


def _project_lanes(
    lanes: list[dict[str, Any]], *, root_id: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    out: list[dict[str, Any]] = []
    by_id: dict[str, str] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lid = str(lane.get("id") or "")
        if not lid:
            continue
        if _lane_resolved(lane):
            compact = _compact_lane_disposition(lane)
        else:
            compact = _compact_lane_actionable(lane, root_id=root_id)
        out.append(compact)
        by_id[lid] = lid
    return out, by_id


def _slim_attention_item(
    item: dict[str, Any],
    *,
    lane_ids: dict[str, str],
    root_id: str,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return item
    kind = item.get("kind")
    lid = str(item.get("id") or "")
    base: dict[str, Any] = {"kind": kind or "lane"}
    if lid:
        base["id"] = lid
    if lid and lid in lane_ids:
        base["lane_ref"] = {"id": lid}
    if kind in _PROTECTED_ATTN_KINDS:
        return {**base, **{k: item[k] for k in item if k not in ("unread_turns",)}}
    if lid and lid in lane_ids:
        return {
            **base,
            "unread": item.get("unread"),
            "lifecycle": item.get("lifecycle"),
        }
    slim = dict(item)
    unread_turns = slim.pop("unread_turns", None)
    if isinstance(unread_turns, list) and unread_turns:
        slim["unread_turns"] = [
            _compact_turn_row(t, thread_id=lid or root_id)
            for t in unread_turns
            if isinstance(t, dict)
        ]
    return slim


def _dedupe_root_notices(
    root: dict[str, Any],
    attention: list[dict[str, Any]],
    *,
    root_id: str,
) -> dict[str, Any]:
    seen: set[str] = set()
    for item in attention:
        if not isinstance(item, dict):
            continue
        lid = str(item.get("id") or root_id)
        for row in item.get("unread_turns") or []:
            if isinstance(row, dict):
                seen.add(_turn_identity(row, thread_id=lid))
    root_out = dict(root)
    for key in ("recent_turns", "unread_turns"):
        rows = root_out.get(key)
        if not isinstance(rows, list):
            continue
        compact: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident = _turn_identity(row, thread_id=root_id)
            if ident in seen:
                continue
            seen.add(ident)
            compact.append(_compact_turn_row(row, thread_id=root_id))
        root_out[key] = compact
    return root_out


def _attention_cap_priority(item: dict[str, Any]) -> int:
    if not isinstance(item, dict):
        return 0
    if item.get("kind") in _PROTECTED_ATTN_KINDS:
        return 10_000
    lc = str(item.get("lifecycle") or "").lower()
    if lc in ("failed", "abandoned") or (item.get("unread") or 0) > 0:
        return 5_000
    return 100


def _apply_cap(
    out: dict[str, Any],
    *,
    root_id: str,
    cap_bytes: int,
    full_digest_sha256: str,
) -> dict[str, Any]:
    lanes = list(out.get("lanes") or [])
    attention = list(out.get("attention") or [])
    continued: list[dict[str, Any]] = list(out.get("lanes_continued") or [])

    def fits() -> bool:
        return len(serialize_agent_digest(out)) <= cap_bytes

    if fits():
        return out

    # Shrink turn bodies in place (lowest priority first).
    for lane in lanes:
        for row in lane.get("unread_turns") or []:
            if isinstance(row, dict) and row.get("body"):
                row["body"] = str(row["body"])[:80]
                row["body_truncated"] = True
                row["body_retrieval"] = _turn_get_retrieval(
                    str(lane.get("id") or root_id), row.get("turn_number")
                )
                if fits():
                    return out

    # Drop lanes by ascending priority (never drop mandatory obligations).
    droppable = sorted(lanes, key=_lane_cap_priority)
    for lane in droppable:
        if _lane_cap_priority(lane) >= 1_000:
            break
        if lane not in lanes:
            continue
        lanes.remove(lane)
        lid = str(lane.get("id") or "")
        continued.append(
            {
                "id": lid,
                "retrieval": _lane_retrieval(lid),
                "reason": "cap_bytes",
            }
        )
        out["lanes"] = lanes
        out["lanes_continued"] = continued
        if fits():
            out["lanes_continued_count"] = len(continued)
            return out

    droppable_attn = sorted(attention, key=_attention_cap_priority)
    for item in droppable_attn:
        if _attention_cap_priority(item) >= 10_000:
            continue
        if item not in attention:
            continue
        attention.remove(item)
        out["attention"] = attention
        out.setdefault("attention_continued", []).append(
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "retrieval": _lane_retrieval(str(item.get("id") or root_id)),
            }
        )
        if fits():
            return out

    out["overflow"] = {
        "reason": "mandatory_exceeds_cap",
        "cap_bytes": cap_bytes,
        "full_digest": {
            "verify_sha256": full_digest_sha256,
            "rebuild_read_only": f"scripts/liaison-tick.py --root {root_id} --once --full-digest",
        },
    }
    return out


def project_liaison_digest_agent(
    digest: dict[str, Any],
    *,
    cap_bytes: int = DEFAULT_AGENT_DIGEST_CAP_BYTES,
) -> dict[str, Any]:
    """Compact digest for agent-facing CLI/tool responses; full digest stays internal."""
    base = project_digest(digest)
    root_id = str((base.get("root") or {}).get("id") or digest.get("root", {}).get("id") or "")
    lanes_in = list(base.get("lanes") or digest.get("lanes") or [])
    lanes, lane_ids = _project_lanes(lanes_in, root_id=root_id)
    attention_in = list(base.get("attention") or [])
    attention = [
        _slim_attention_item(item, lane_ids=lane_ids, root_id=root_id)
        for item in attention_in
        if isinstance(item, dict)
    ]
    merged_root = dict(base.get("root") or {})
    raw_root = digest.get("root") if isinstance(digest.get("root"), dict) else {}
    for key in ("recent_turns", "unread_turns"):
        rows = raw_root.get(key)
        if isinstance(rows, list):
            merged_root[key] = rows
    root = _dedupe_root_notices(merged_root, attention, root_id=root_id)
    fp_src = json.dumps(digest, sort_keys=True, default=str).encode("utf-8")
    full_sha = hashlib.sha256(fp_src).hexdigest()
    out: dict[str, Any] = {
        **base,
        "root": root,
        "lanes": lanes,
        "attention": attention,
        "projection": {
            "source": "digest_agent_projection",
            "full_digest_sha256": full_sha,
            "cap_bytes": cap_bytes,
            "full_digest_rebuild_read_only": (
                f"scripts/liaison-tick.py --root {root_id} --once --full-digest"
                if root_id
                else None
            ),
        },
    }
    out.pop("unread_toc", None)
    return _apply_cap(out, root_id=root_id, cap_bytes=cap_bytes, full_digest_sha256=full_sha)


def render_liaison_digest_agent_json(
    digest: dict[str, Any],
    *,
    cap_bytes: int = DEFAULT_AGENT_DIGEST_CAP_BYTES,
) -> str:
    proj = project_liaison_digest_agent(digest, cap_bytes=cap_bytes)
    return serialize_agent_digest(proj).decode("utf-8")


__all__ = [
    "DEFAULT_AGENT_DIGEST_CAP_BYTES",
    "project_liaison_digest_agent",
    "render_liaison_digest_agent_json",
    "serialize_agent_digest",
    "_compact_turn_row",
    "_lane_resolved",
    "_project_lanes",
    "_slim_attention_item",
]
