"""Detect agent-bus threads that are houseless mailboxes (first-seat birth lanes).

Predicate (structural, not purpose-tagged):
- folded ``parent_thread`` is empty
- ``role:root`` is absent (not an orchestration house spine)

Does not require a ``houseless`` registry purpose tag; auto-tagging is a separate
ruling.
"""

from __future__ import annotations

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
    return ROLE_ROOT_TAG not in tags
