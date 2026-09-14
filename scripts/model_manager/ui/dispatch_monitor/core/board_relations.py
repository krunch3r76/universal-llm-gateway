"""Relation tags for SDK live lines — paint evidence-backed edges on the View."""

from __future__ import annotations

from collections import defaultdict

from .dtos import RelationEdge, SdkDispatchRow
from .watch import clip_text

_INBOUND_KINDS = frozenset(
    {"nest_under", "review_child", "lease_park", "root_dispatch"}
)


def _inbound_by_child(
    relations: tuple[RelationEdge, ...],
) -> dict[str, list[RelationEdge]]:
    inbound: dict[str, list[RelationEdge]] = defaultdict(list)
    for edge in relations:
        if edge.kind in _INBOUND_KINDS:
            inbound[edge.to_id].append(edge)
    return inbound


def sdk_relation_tags(
    row: SdkDispatchRow,
    relations: tuple[RelationEdge, ...] | None,
) -> list[str]:
    """Compact child-of labels for one live SDK row."""
    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)

    has_root_dispatch = False
    if relations:
        for edge in _inbound_by_child(relations).get(row.dispatch_id, ()):
            parent = clip_text(edge.from_id, 14)
            if edge.kind == "root_dispatch":
                has_root_dispatch = True
                add(f"child-of:t={clip_text(edge.from_id, 8)}")
            elif edge.kind == "nest_under":
                add(f"child-of:nest={parent}")
            elif edge.kind == "review_child":
                add(f"child-of:review={parent}")
            elif edge.kind == "lease_park":
                add(f"child-of:park={parent}")

        for edge in relations:
            if edge.kind == "resume_of" and edge.from_id == row.dispatch_id:
                add(f"resume-of={clip_text(edge.to_id, 14)}")

    if (
        not has_root_dispatch
        and row.root_id
        and row.thread_id
        and row.thread_id != row.root_id
    ):
        add(f"child-of:t={clip_text(row.root_id, 8)}")

    return tags
