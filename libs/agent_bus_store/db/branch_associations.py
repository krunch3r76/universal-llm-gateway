"""Append-only lane↔branch association store helpers.

Participates in store-allocated ordering (SQLite AUTOINCREMENT ``id``) and
derived-current reads via ``MAX(id)`` per ``thread_id``. No UPDATE-for-current.
"""

from __future__ import annotations

from typing import Any, Literal

from ..events.branch_associated import emit_branch_associated
from .connection import connect
from .threads import get_thread, normalize_thread_id

AssociationState = Literal["none", "associated"]


class ClientOrderingTokenError(ValueError):
    """Raised when associate payload carries a client-supplied ordering token."""


def associate_branch(*, thread_id: str, branch_name: str) -> dict[str, Any]:
    """Append one association row; return insert echo plus derived current branch.

    Rejects empty branch names. Ordering is store-allocated via AUTOINCREMENT id.
    """
    thread_id = normalize_thread_id(thread_id)
    branch = branch_name.strip()
    if not branch:
        raise ValueError("branch_name must be non-empty")

    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO thread_branch_associations (thread_id, branch_name) "
            "VALUES (?, ?)",
            (thread_id, branch),
        )
        association_id = int(cur.lastrowid)

    current = get_current_branch(thread_id=thread_id)
    emit_branch_associated(
        thread_id=thread_id,
        branch_name=branch,
        association_id=association_id,
    )
    return {
        "thread_id": thread_id,
        "branch_name": branch,
        "id": association_id,
        "current_branch": current["current_branch"],
    }


def get_current_branch(*, thread_id: str) -> dict[str, Any]:
    """Return derived current branch for a lane from append-only association history."""
    thread_id = normalize_thread_id(thread_id)

    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, branch_name FROM thread_branch_associations "
            "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()

    if row is None:
        return {
            "thread_id": thread_id,
            "current_branch": None,
            "association_id": None,
            "state": "none",
        }

    return {
        "thread_id": thread_id,
        "current_branch": row["branch_name"],
        "association_id": int(row["id"]),
        "state": "associated",
    }


def reject_client_ordering_tokens(payload: dict[str, Any]) -> None:
    """Reject associate bodies that supply store-owned ordering fields."""
    forbidden = ("id", "seq")
    present = [key for key in forbidden if key in payload]
    if present:
        raise ClientOrderingTokenError(
            f"Client-supplied ordering tokens not allowed: {', '.join(present)}"
        )
