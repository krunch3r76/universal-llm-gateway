"""Resolve the nested-SDK row a cursor-auto poll should watch.

``dispatch_status_by_thread`` is newest-row-wins. After a ``park_for_restart``
resume child admits on the same thread, a poller still bound to the parent id
never sees the child's ``completed`` (specimen 2026-09-20 AutoJob 765c56f3
watching ``auto-a479e8e67316`` after ``-r1`` closed). Do not treat ledger
``cancelled`` as terminal — follow ``park_resumed_by`` instead.
"""

from __future__ import annotations

from typing import Any


def resolve_nested_poll_row(
    ledger: Any, *, thread_id: str, dispatch_id: str
) -> dict[str, Any] | None:
    """Return the status row for *dispatch_id*, or its resume child if parked."""
    row = ledger.dispatch_status_by_id(dispatch_id=dispatch_id)
    if row is None:
        row = ledger.dispatch_status_by_thread(thread_id=thread_id)
    if row is None:
        return None
    if str(row.get("status") or "") != "cancelled":
        return row
    child = _park_resume_child(str(row.get("dispatch_id") or dispatch_id))
    if not child:
        return row
    followed = ledger.dispatch_status_by_id(dispatch_id=child)
    return followed if followed is not None else row


def _park_resume_child(dispatch_id: str) -> str | None:
    from services.git_integration_worker.cursor_sdk_park_ledger import load_park_row

    park = load_park_row(dispatch_id=dispatch_id)
    child = (park.park_resumed_by if park is not None else None) or ""
    return child.strip() or None
