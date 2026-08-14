"""Lane partition render helpers for CHECKPOINT derived zones (S1/S3)."""

from __future__ import annotations

_CLOSED_THREAD_STATUSES = frozenset({"closed"})


def render_substantiated_child(row, *, compressed: bool) -> str:
    if compressed and row.status.lower() in _CLOSED_THREAD_STATUSES:
        return f"- agent-bus:{row.thread_id} closed@{row.last_turn}"
    role = row.lane_role or "unknown"
    return (
        f"- agent-bus:{row.thread_id} · {role} · {row.status} · turn {row.last_turn}"
    )


def render_cited_lane(row, *, compressed: bool) -> str:
    if compressed and row.status.lower() in _CLOSED_THREAD_STATUSES:
        return f"- agent-bus:{row.thread_id} closed@{row.last_turn}"
    if row.lane_role and row.parent_thread_id:
        return (
            f"- agent-bus:{row.thread_id} · {row.lane_role} of "
            f"agent-bus:{row.parent_thread_id} · {row.status} · turn {row.last_turn}"
        )
    return (
        f"- agent-bus:{row.thread_id} · unassociated · {row.status} · "
        f"turn {row.last_turn}"
    )


def render_lane_derived_sections(
    *,
    child_lanes: tuple,
    cited_lanes: tuple,
    compress_closed_children: bool,
) -> list[str]:
    """Return markdown lines for Child lanes + Cited lanes subsections."""
    parts = ["### Child lanes"]
    if child_lanes:
        for child in child_lanes:
            parts.append(
                render_substantiated_child(
                    child,
                    compressed=compress_closed_children
                    and child.status.lower() in _CLOSED_THREAD_STATUSES,
                )
            )
    else:
        parts.append("_none substantiated_")
    parts.append("")
    parts.append("### Cited lanes")
    if cited_lanes:
        for cited in cited_lanes:
            parts.append(
                render_cited_lane(
                    cited,
                    compressed=compress_closed_children
                    and cited.status.lower() in _CLOSED_THREAD_STATUSES,
                )
            )
    else:
        parts.append("_none cited_")
    return parts


__all__ = [
    "render_cited_lane",
    "render_lane_derived_sections",
    "render_substantiated_child",
]
