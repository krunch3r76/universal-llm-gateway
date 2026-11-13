"""Detect agent-bus threads that are houseless mailboxes (first-seat birth lanes).

Predicate (mailbox tag + structural):
- ``mailbox`` tag present on the bus thread
- folded ``parent_thread`` is empty
- ``role:root`` is absent (not an orchestration house spine)
- generate ``mission_kind`` is not ``hop`` (enforced by caller)

Does not require a ``houseless`` registry purpose tag; auto-tagging is a separate
ruling.
"""

from __future__ import annotations

from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import get_thread, normalize_thread_id
from agent_bus_store.thread_classification import ROLE_ROOT_TAG


def is_houseless_mailbox_thread(thread_id: str) -> bool:
    """Return True when *thread_id* names a parentless work mailbox thread."""
    tid = normalize_thread_id(thread_id)
    if not tid:
        return False
    try:
        detail = get_thread(tid)
    except Exception:  # noqa: BLE001 — seating bind is fail-soft
        return False
    if detail is None:
        return False
    if str(detail.get("parent_thread") or "").strip():
        return False
    tags = detail.get("tags") or []
    if ROLE_ROOT_TAG in tags:
        return False
    return "mailbox" in tags


def _caller_slug_candidates(caller_agent: str) -> list[str]:
    agent = (caller_agent or "").strip()
    if not agent:
        return []
    out = [agent]
    if ":" in agent:
        tail = agent.split(":")[-1].strip()
        if tail and tail not in out:
            out.append(tail)
    return out


def resolve_mailbox_thread(
    *,
    mailbox_thread: str | None,
    caller_agent: str | None,
    thread_id: str | None,
) -> str | None:
    """Resolve the bus mailbox lane for houseless first-seat registration."""
    raw_mailbox = (mailbox_thread or "").strip()
    explicit = normalize_thread_id(raw_mailbox) if raw_mailbox else None
    if explicit and is_houseless_mailbox_thread(explicit):
        return explicit
    for slug in _caller_slug_candidates(caller_agent or ""):
        try:
            with connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM threads WHERE slug = ? COLLATE NOCASE",
                    (slug,),
                ).fetchall()
        except Exception:  # noqa: BLE001 — resolve is best-effort at admit
            continue
        matches = [
            str(row["id"])
            for row in rows
            if is_houseless_mailbox_thread(str(row["id"]))
        ]
        if len(matches) == 1:
            return matches[0]
    raw_tid = (thread_id or "").strip()
    fallback = normalize_thread_id(raw_tid) if raw_tid else None
    if fallback and is_houseless_mailbox_thread(fallback):
        return fallback
    return None
