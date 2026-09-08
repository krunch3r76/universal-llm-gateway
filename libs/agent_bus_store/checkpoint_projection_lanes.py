"""Lane partition render helpers for CHECKPOINT derived zones (S1/S3)."""

from __future__ import annotations

from .checkpoint_projection_producers import (
    ProducerDispatchRow,
    render_producers_section,
    transcript_projection_registry_uri,
)

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


def _partition_child_lanes(child_lanes: tuple) -> tuple[tuple, tuple]:
    active: list = []
    closed: list = []
    for child in child_lanes:
        if child.status.lower() in _CLOSED_THREAD_STATUSES:
            closed.append(child)
        else:
            active.append(child)
    return tuple(active), tuple(closed)


def _render_child_lanes_summary(
    *,
    child_lanes: tuple,
    root_thread: str,
) -> list[str]:
    """Tier 2/3: active sub_missions listed; closed collapsed to count."""
    parts = ["### Child lanes"]
    if not child_lanes:
        parts.append("_none substantiated_")
        return parts
    active, closed = _partition_child_lanes(child_lanes)
    for child in active:
        parts.append(render_substantiated_child(child, compressed=False))
    registry = transcript_projection_registry_uri(root_thread)
    parts.append(
        f"child_lanes: {len(active)} active · {len(closed)} closed · "
        f"registry: {registry}"
    )
    return parts


def render_lane_derived_sections(
    *,
    root_thread: str,
    child_lanes: tuple,
    cited_lanes: tuple,
    producer_rows: tuple[ProducerDispatchRow, ...],
    compress_closed_children: bool,
    summary_mode: bool = True,
) -> list[str]:
    """Return markdown lines for Child lanes, In-flight producers, Cited lanes."""
    if summary_mode:
        parts = _render_child_lanes_summary(
            child_lanes=child_lanes,
            root_thread=root_thread,
        )
        parts.append("")
        parts.extend(
            render_producers_section(
                producer_rows,
                root_thread=root_thread,
                summary_mode=True,
            )
        )
        parts.append("")
        parts.append("### Cited lanes")
        if cited_lanes:
            active, closed = _partition_child_lanes(cited_lanes)
            for cited in active:
                parts.append(render_cited_lane(cited, compressed=False))
            if closed:
                parts.append(f"_+{len(closed)} closed cited lanes_")
        else:
            parts.append("_none cited_")
        return parts

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
    parts.extend(
        render_producers_section(
            producer_rows,
            root_thread=root_thread,
            summary_mode=False,
        )
    )
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
