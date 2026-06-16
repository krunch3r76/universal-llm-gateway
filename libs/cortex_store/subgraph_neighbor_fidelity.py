"""Depth-aware neighbor fidelity for subgraph rendering.

Branches Card v0 fetch depth and markdown template shape by hop distance
and hub density. Extracted from :mod:`subgraph_renderer` per modularization
gate (renderer was ~310 SLOC before this slice).
"""

from __future__ import annotations

from typing import Any, Literal

NeighborFidelity = Literal["full", "depth_aware", "edges_only"]

_HUB_REL_THRESHOLD_DEFAULT = 20


def hub_rel_threshold_default() -> int:
    return _HUB_REL_THRESHOLD_DEFAULT


def is_hub(rel_count: int, *, hub_rel_threshold: int) -> bool:
    return rel_count >= hub_rel_threshold


def card_top_k_for_entity(
    *,
    entity_id: str,
    root: str,
    hop: int,
    fidelity: NeighborFidelity,
    top_k_root: int,
    hub_rel_threshold: int,
    rel_count: int,
) -> int:
    """Assertion fetch depth for one visited entity under ``fidelity``."""
    if fidelity == "full":
        return top_k_root
    if entity_id == root:
        return top_k_root
    if is_hub(rel_count, hub_rel_threshold=hub_rel_threshold):
        return 1
    return 0


def neighbor_block_mode(
    *,
    entity_id: str,
    root: str,
    hop: int,
    fidelity: NeighborFidelity,
    hub_rel_threshold: int,
    rel_count: int,
) -> Literal["full", "hop1_sparse", "hop2_sparse", "hub_promoted"]:
    """Markdown block shape for a non-root entity."""
    if fidelity == "full":
        return "full"
    if entity_id == root:
        return "full"
    hub = is_hub(rel_count, hub_rel_threshold=hub_rel_threshold)
    if hop <= 1:
        return "hub_promoted" if hub else "hop1_sparse"
    return "hop2_sparse"


def sparse_card_shell(
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    active_assertion_count: int,
    rel_count: int,
    summary_row: str | None = None,
    top_assertion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal card-shaped dict without full Card v0 fetch."""
    card: dict[str, Any] = {
        "entity_id": entity_id,
        "type": entity_type,
        "name": name,
        "active_assertion_count": active_assertion_count,
        "relationship_count": rel_count,
        "top_k_assertions": [top_assertion] if top_assertion else [],
        "freshness": {},
    }
    if summary_row:
        card["summary_row"] = summary_row
    return card
