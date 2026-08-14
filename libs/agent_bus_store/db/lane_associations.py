"""Append-only lane parentage association store helpers.

Participates in store-allocated ordering (SQLite AUTOINCREMENT ``id``) and
derived-current reads via ``MAX(id)`` per ``thread_id``. Append only — never
mutate a current-parent pointer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from ..events.lane_bound import emit_lane_bound
from ..lane_roles import parse_lane_role
from .connection import connect
from .threads import get_thread, normalize_thread_id

AssociationState = Literal["none", "associated"]


class ClientOrderingTokenError(ValueError):
    """Raised when associate payload carries a client-supplied ordering token."""


class LaneBindCreate(BaseModel):
    """Lane-bind payload — store allocates ``id``; client must not supply ordering."""

    model_config = {"extra": "forbid"}

    parent_thread_id: str
    lane_role: str
    bound_by: str | None = None
    evidence: str | None = None


class LaneBindResponse(BaseModel):
    """POST lane-bind echo plus derived current pair — no clock fields."""

    thread_id: str
    parent_thread_id: str
    lane_role: str
    id: int
    state: AssociationState


class LaneCurrentResponse(BaseModel):
    """Derived current lane parentage read — ``state=none`` when never bound."""

    thread_id: str
    parent_thread: str | None
    lane_role: str | None
    association_id: int | None
    state: AssociationState


def reject_client_ordering_tokens(payload: dict[str, Any]) -> None:
    """Reject associate bodies that supply store-owned ordering fields."""
    forbidden = ("id", "seq")
    present = [key for key in forbidden if key in payload]
    if present:
        raise ClientOrderingTokenError(
            f"Client-supplied ordering tokens not allowed: {', '.join(present)}"
        )


def _prior_association_id(conn, *, thread_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM thread_lane_associations "
        "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def associate_lane(
    *,
    thread_id: str,
    parent_thread_id: str,
    lane_role: str,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Append one association row; return insert echo plus derived current pair."""
    thread_id = normalize_thread_id(thread_id)
    parent_thread_id = normalize_thread_id(parent_thread_id)
    role = parse_lane_role(lane_role)

    if thread_id == parent_thread_id:
        raise ValueError("thread_id must not equal parent_thread_id")

    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")
    if get_thread(parent_thread_id) is None:
        raise LookupError(f"Thread {parent_thread_id} not found")

    with connect() as conn:
        prior_id = _prior_association_id(conn, thread_id=thread_id)
        cur = conn.execute(
            "INSERT INTO thread_lane_associations "
            "(thread_id, parent_thread_id, lane_role, bound_by, evidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, parent_thread_id, role, bound_by, evidence),
        )
        association_id = int(cur.lastrowid)

    emit_lane_bound(
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        lane_role=role,
        association_id=association_id,
        prior_association_id=prior_id,
        bound_by=bound_by,
    )
    current = get_current_lane(thread_id=thread_id)
    return {
        "thread_id": thread_id,
        "parent_thread_id": parent_thread_id,
        "lane_role": role,
        "id": association_id,
        "state": current["state"],
    }


def get_current_lane(*, thread_id: str) -> dict[str, Any]:
    """Return derived current lane parentage for a thread from append-only history."""
    thread_id = normalize_thread_id(thread_id)

    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    with connect() as conn:
        row = conn.execute(
            "SELECT id, parent_thread_id, lane_role FROM thread_lane_associations "
            "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()

    if row is None:
        return {
            "thread_id": thread_id,
            "parent_thread": None,
            "lane_role": None,
            "association_id": None,
            "state": "none",
        }

    return {
        "thread_id": thread_id,
        "parent_thread": row["parent_thread_id"],
        "lane_role": row["lane_role"],
        "association_id": int(row["id"]),
        "state": "associated",
    }


def merge_lane_fields(rows: list[dict[str, Any]]) -> None:
    """In-place merge folded ``parent_thread`` + ``lane_role`` onto thread rows."""
    if not rows:
        return
    thread_ids = [str(row["id"]) for row in rows if row.get("id") is not None]
    if not thread_ids:
        thread_ids = [str(row["thread"]) for row in rows if row.get("thread")]
    if not thread_ids:
        return
    placeholders = ",".join("?" * len(thread_ids))
    sql = f"""
        SELECT tla.thread_id, tla.parent_thread_id, tla.lane_role
        FROM thread_lane_associations tla
        INNER JOIN (
            SELECT thread_id, MAX(id) AS max_id
            FROM thread_lane_associations
            GROUP BY thread_id
        ) latest
            ON tla.thread_id = latest.thread_id AND tla.id = latest.max_id
        WHERE tla.thread_id IN ({placeholders})
    """
    with connect() as conn:
        lane_rows = conn.execute(sql, thread_ids).fetchall()
    lane_map = {
        row["thread_id"]: {
            "parent_thread": row["parent_thread_id"],
            "lane_role": row["lane_role"],
        }
        for row in lane_rows
    }
    for row in rows:
        key = str(row.get("id") or row.get("thread"))
        folded = lane_map.get(key)
        if folded is None:
            row["parent_thread"] = None
            row["lane_role"] = None
        else:
            row["parent_thread"] = folded["parent_thread"]
            row["lane_role"] = folded["lane_role"]


def list_substantiated_child_thread_ids(*, parent_thread_id: str) -> tuple[str, ...]:
    """Return thread ids whose folded parent is ``parent_thread_id`` (depth-1)."""
    parent_thread_id = normalize_thread_id(parent_thread_id)
    sql = """
        SELECT tla.thread_id
        FROM thread_lane_associations tla
        INNER JOIN (
            SELECT thread_id, MAX(id) AS max_id
            FROM thread_lane_associations
            GROUP BY thread_id
        ) latest
            ON tla.thread_id = latest.thread_id AND tla.id = latest.max_id
        WHERE tla.parent_thread_id = ?
        ORDER BY tla.thread_id
    """
    with connect() as conn:
        rows = conn.execute(sql, (parent_thread_id,)).fetchall()
    return tuple(str(row["thread_id"]) for row in rows)


def invalid_lane_role_envelope(*, lane_role: str, reason: str) -> dict[str, object]:
    """Structured 422 detail for unknown lane roles."""
    return {
        "code": "invalid_lane_role",
        "message": reason,
        "retryable": False,
        "source": "agent_bus_store.lane_associations",
        "data": {"lane_role": lane_role},
    }


def lane_bind_incomplete_envelope(*, provided: list[str]) -> dict[str, object]:
    """Structured 422 when only one of parent_thread / lane_role is supplied."""
    return {
        "code": "lane_bind_incomplete",
        "message": "parent_thread and lane_role must both be supplied or both omitted",
        "retryable": False,
        "source": "agent_bus_store.routes.threads",
        "data": {"provided": provided},
    }
