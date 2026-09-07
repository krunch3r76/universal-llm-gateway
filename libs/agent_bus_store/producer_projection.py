"""Producer-link projection for the agent-bus wait endpoint."""

from __future__ import annotations

from typing import Any, Literal

ProducerState = Literal["unknown", "unlinked", "in_flight", "terminal"]

_SOURCE = "thread_dispatch_links"


def classify_producer_link(
    *,
    execution_id: str | None,
    dispatch_links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify dispatch-link liveness for one pinned execution.

    Always returns the advisory ``producer`` container with keys:
    ``execution_id``, ``pipeline_id``, ``state``, ``terminal_status``,
    ``linked_at``, ``delivery_at``, ``source``.
    """
    if not execution_id:
        return {
            "execution_id": None,
            "pipeline_id": None,
            "state": "unknown",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
        }

    row = next(
        (link for link in dispatch_links if link.get("execution_id") == execution_id),
        None,
    )
    if row is None:
        return {
            "execution_id": execution_id,
            "pipeline_id": None,
            "state": "unlinked",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
        }

    terminal_status = row.get("terminal_status")
    state: ProducerState = "terminal" if terminal_status else "in_flight"
    return {
        "execution_id": execution_id,
        "pipeline_id": row.get("pipeline_id"),
        "state": state,
        "terminal_status": terminal_status,
        "linked_at": row.get("linked_at"),
        "delivery_at": row.get("delivery_at"),
        "source": _SOURCE,
    }
