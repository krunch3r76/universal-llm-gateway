"""Depth-1 SDK nest tree — ordering, glyphs, and orphan pointers (G5.2)."""

from __future__ import annotations

from .dtos import SdkDispatchRow


def _live_ids(live: list[SdkDispatchRow]) -> set[str]:
    return {row.dispatch_id for row in live}


def nest_under_edges(live: list[SdkDispatchRow]) -> dict[str, str]:
    """Return child→parent map for live ``nest_under`` edges (depth-1 only)."""
    ids = _live_ids(live)
    edges: dict[str, str] = {}
    for row in live:
        parent = row.nest_under
        if not parent or parent == row.dispatch_id or parent not in ids:
            continue
        edges[row.dispatch_id] = parent
    return edges


def cycle_nodes(edges: dict[str, str]) -> set[str]:
    cyclic: set[str] = set()
    for start in edges:
        chain: list[str] = []
        cur: str | None = start
        while cur is not None and cur in edges:
            if cur in chain:
                cyclic.update(chain[chain.index(cur) :])
                cyclic.add(cur)
                break
            chain.append(cur)
            cur = edges.get(cur)
    return cyclic


def resolved_nest_members(live: list[SdkDispatchRow]) -> set[str]:
    """Dispatch ids participating in a live ``nest_under`` parent↔child family."""
    edges = nest_under_edges(live)
    cyclic = cycle_nodes(edges)
    members: set[str] = set()
    for child, parent in edges.items():
        if child in cyclic:
            continue
        members.add(child)
        members.add(parent)
    return members


def tree_glyph(
    row: SdkDispatchRow,
    *,
    edges: dict[str, str],
    cyclic: set[str],
) -> str:
    """Return ``└─ `` for a depth-1 child, else two spaces."""
    if row.dispatch_id in edges and row.dispatch_id not in cyclic:
        return "└─ "
    return "  "


def nest_pointer(
    row: SdkDispatchRow,
    *,
    live_ids: set[str],
    edges: dict[str, str],
    cyclic: set[str],
) -> str:
    """Orphan / cycle degrade — ``↳ <parent>`` without hanging the tree."""
    parent = row.nest_under
    if not parent:
        return ""
    if row.dispatch_id in cyclic:
        return f"↳ {parent}"
    if parent not in live_ids:
        return f"↳ {parent}"
    if parent == row.dispatch_id:
        return f"↳ {parent}"
    return ""


def sort_sdk_tree(live: list[SdkDispatchRow]) -> list[SdkDispatchRow]:
    """Order live rows parent→child within each nest family; preserve peer order."""
    edges = nest_under_edges(live)
    cyclic = cycle_nodes(edges)
    child_ids = {cid for cid in edges if cid not in cyclic}
    children_by_parent: dict[str, list[SdkDispatchRow]] = {}
    for row in live:
        if row.dispatch_id in child_ids:
            parent = edges[row.dispatch_id]
            children_by_parent.setdefault(parent, []).append(row)

    ordered: list[SdkDispatchRow] = []
    placed: set[str] = set()
    for row in live:
        if row.dispatch_id in placed or row.dispatch_id in child_ids:
            continue
        ordered.append(row)
        placed.add(row.dispatch_id)
        for child in children_by_parent.get(row.dispatch_id, []):
            if child.dispatch_id not in placed:
                ordered.append(child)
                placed.add(child.dispatch_id)
    for row in live:
        if row.dispatch_id not in placed:
            ordered.append(row)
    return ordered
