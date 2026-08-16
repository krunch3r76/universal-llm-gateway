"""Live, side-effect-free thread lineage read: dispatch links + lane children.

Single combined primitive for "what is attached to this thread" — the
checkpoint projection's substantiated-child registry (``checkpoint_projection_wiring.py``)
and the ``GET /threads/{id}/lineage`` route both call ``get_thread_lineage``
instead of independently re-deriving the same two joins.
"""

from __future__ import annotations

from dataclasses import dataclass

from .connection import connect
from .lane_associations import get_current_lane, list_substantiated_child_thread_ids
from .threads import get_thread, load_dispatch_links, normalize_thread_id
from .turns import get_thread_turn_count


@dataclass(frozen=True, slots=True)
class LineageChild:
    """One lane-substantiated child thread, enriched for display/projection."""

    thread_id: str
    status: str
    turn_count: int
    lane_role: str | None
    parent_thread_id: str | None


@dataclass(frozen=True, slots=True)
class LineageDispatchLink:
    """One dispatch-execution link row for the queried thread."""

    execution_id: str
    pipeline_id: str
    linked_at: str
    terminal_status: str | None
    delivery_at: str | None


@dataclass(frozen=True, slots=True)
class ThreadLineage:
    """Combined lineage view for one thread: its children and dispatch links."""

    thread_id: str
    children: tuple[LineageChild, ...]
    dispatch_links: tuple[LineageDispatchLink, ...]


def _lineage_child(thread_id: str) -> LineageChild | None:
    row = get_thread(thread_id)
    if row is None:
        return None
    lane = get_current_lane(thread_id=thread_id)
    return LineageChild(
        thread_id=thread_id,
        status=str(row.get("status", "unknown")),
        turn_count=get_thread_turn_count(thread_id),
        lane_role=lane.get("lane_role"),
        parent_thread_id=lane.get("parent_thread"),
    )


def get_thread_lineage(thread_id: str) -> ThreadLineage | None:
    """Return substantiated lane children + dispatch links for one thread.

    Zero side effects; safe to call on every checkpoint post and on-demand
    via the API. Returns None when the thread does not exist.
    """
    thread_id = normalize_thread_id(thread_id)
    if get_thread(thread_id) is None:
        return None

    child_ids = list_substantiated_child_thread_ids(parent_thread_id=thread_id)
    children = tuple(
        child for child in (_lineage_child(cid) for cid in child_ids) if child is not None
    )

    with connect() as conn:
        raw_links = load_dispatch_links(conn, thread_id)
    dispatch_links = tuple(
        LineageDispatchLink(
            execution_id=link["execution_id"],
            pipeline_id=link["pipeline_id"],
            linked_at=link["linked_at"],
            terminal_status=link.get("terminal_status"),
            delivery_at=link.get("delivery_at"),
        )
        for link in raw_links
    )

    return ThreadLineage(
        thread_id=thread_id,
        children=children,
        dispatch_links=dispatch_links,
    )
