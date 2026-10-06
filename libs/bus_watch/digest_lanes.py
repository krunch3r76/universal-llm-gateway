"""Lane listing for liaison digest — child, lineage, unread TOC."""

from __future__ import annotations

import re
from typing import Any

import httpx

from bus_watch.digest_unread import DIGEST_UNREAD_TO, lane_unread_fields
from bus_watch.quiet_reason import stamp_quiet_reason, usable_holder_id


def _get(client: httpx.Client, path: str, **params: object) -> dict[str, Any] | None:
    from bus_watch.liaison_digest import _get as fetch

    return fetch(client, path, **params)


_TERMINAL_RE = re.compile(
    r"CLOSEOUT|status:done|status:failed|status:needs-attended|SCORE_RESURFACE|"
    r"stall-pop|PARKED|FAILED|CHECKPOINT|BRIDGE_ACK|Dispatch orphaned|holder_lost",
    re.I,
)
_NAG_RE = re.compile(r"^branch-debt\b", re.I)
_NAG_SENDERS = frozenset({"git-integration-worker"})
_MAX_LANES = 25
_MAX_LINEAGE = 40
_SUBJECT_CAP = 120
_WORKER_RE = re.compile(r"Worker thread `(\d+)`")


def _contract_from_thread(t: dict[str, Any]) -> str:
    raw = t.get("contract")
    if raw:
        return str(raw).strip().lower()
    for tag in t.get("tags") or []:
        token = str(tag)
        if token.startswith("contract:"):
            return token.split(":", 1)[1].strip().lower()
    return ""


def _lane_row(t: dict[str, Any]) -> dict[str, Any]:
    subject = str(t.get("last_subject") or "")[:_SUBJECT_CAP]
    row: dict[str, Any] = {
        "id": str(t.get("id")),
        "slug": t.get("slug"),
        "status": t.get("status"),
        "lifecycle": t.get("bus_lifecycle_state"),
        "lane_role": t.get("lane_role"),
        "turns": t.get("turn_count"),
        "last_from": t.get("last_turn_from"),
        "last_subject": subject,
        "contract": _contract_from_thread(t),
        "tags": [str(tag) for tag in (t.get("tags") or [])],
        "terminal": bool(_TERMINAL_RE.search(subject)),
        "nag": bool(_NAG_RE.search(subject))
        and t.get("last_turn_from") in _NAG_SENDERS,
        "updated_at": t.get("updated_at"),
        **lane_unread_fields(t),
    }
    execution_id = usable_holder_id(t.get("execution_id"))
    dispatch_id = usable_holder_id(t.get("dispatch_id"))
    if execution_id:
        row["execution_id"] = execution_id
    if dispatch_id:
        row["dispatch_id"] = dispatch_id
    return row


def _linked_worker_ids(client: httpx.Client, root: str) -> set[str]:
    turns = _get(client, "/turns", thread=root, last=40) or {}
    ids: set[str] = set()
    for turn in turns.get("turns") or []:
        for m in _WORKER_RE.finditer(str(turn.get("body") or "")):
            ids.add(m.group(1))
    return ids


def _child_lanes(client: httpx.Client, root: str) -> list[dict[str, Any]]:
    """Children, grandchildren, and admit-linked workers of ``root``; bounded."""
    act = (
        _get(client, "/threads", status="active", limit=400, to=DIGEST_UNREAD_TO) or {}
    )
    # Discovery stays thread-wide (blocked/waiting lanes with any unstamped
    # turn). Those rows carry recipient=null and label unstamped; scoped
    # act rows merge last so they win on overlap.
    unr = _get(client, "/threads", has_unread=True, limit=100) or {}
    rows = list(
        {str(t["id"]): t for s in (unr, act) for t in (s.get("threads") or [])}.values()
    )
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
        row = by_id.get(tid) or _get(client, f"/threads/{tid}", to=DIGEST_UNREAD_TO)
        if row and "_error" not in row:
            seen.add(tid)
            out.append(
                {**_lane_row(row), "lane_role": row.get("lane_role") or "linked_worker"}
            )
    for lane in out:
        stamp_quiet_reason(client, lane)
    out.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    out.sort(key=lambda r: bool(r.get("nag")))
    return out[:_MAX_LANES]


def _lineage_lanes(client: httpx.Client, root: str) -> list[dict[str, Any]]:
    """Closed-inclusive descendants via ``GET /threads/{id}/lineage``."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    frontier = [root]
    while frontier and len(out) < _MAX_LINEAGE:
        nxt: list[str] = []
        for parent in frontier:
            lin = _get(client, f"/threads/{parent}/lineage") or {}
            for child in lin.get("children") or []:
                if not isinstance(child, dict):
                    continue
                tid = str(child.get("thread_id") or "")
                if not tid or tid in seen or tid == root:
                    continue
                seen.add(tid)
                row = _get(client, f"/threads/{tid}", to=DIGEST_UNREAD_TO)
                if row and "_error" not in row:
                    out.append(_lane_row(row))
                nxt.append(tid)
        frontier = nxt
    return out


def _merge_observe_lanes(
    live: list[dict[str, Any]], lineage: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen = {str(row.get("id")) for row in live if row.get("id")}
    merged = list(live)
    for row in lineage:
        tid = str(row.get("id") or "")
        if tid and tid not in seen:
            seen.add(tid)
            merged.append(row)
    return merged


def _unread_toc(client: httpx.Client, lane_ids: set[str]) -> list[dict[str, Any]]:
    toc = _get(client, "/turns/unread-toc", to=DIGEST_UNREAD_TO, limit=100) or {}
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
