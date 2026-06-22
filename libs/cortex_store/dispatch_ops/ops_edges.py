"""Reasoning edge ops — create/list/traverse/retire/types/impact."""

from __future__ import annotations

from typing import Any

from ..routes.edges import (
    _create_edge_impl,
    _list_edge_types_impl,
    _list_edges_impl,
    _retire_edge_impl,
    _traverse_edges_impl,
    _update_edge_impl,
)
from ..routes.graph import impact_analysis
from ._shared import record


def _op_edge_create(
    session_id: str | None = None,
    agent: str | None = None,
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    strength: float | None = None,
    edge_source: str | None = None,
    context: str | None = None,
    prompt: str | None = None,
    seeded_by: str | None = None,
    metadata: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "from_node": from_node,
        "to_node": to_node,
        "edge_type": edge_type,
    }
    for key, val in [
        ("strength", strength),
        ("edge_source", edge_source),
        ("context", context),
        ("prompt", prompt),
        ("seeded_by", seeded_by),
        ("metadata", metadata),
    ]:
        if val is not None:
            body[key] = val
    result = _create_edge_impl(body)
    if "error" not in result:
        record(
            "mcp.cortex.edge.created",
            session_id=session_id,
            edge_type=edge_type,
            from_node=from_node,
            to_node=to_node,
        )
    return result


def _op_edges(
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    include_retired: bool | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    return _list_edges_impl(
        from_node=from_node,
        to_node=to_node,
        edge_type=edge_type,
        agent=agent,
        session_id=session_id,
        include_retired=bool(include_retired),
        limit=limit or 50,
    )


def _op_edge_traverse(
    node: str | None = None,
    hops: int | None = None,
    edge_type: str | None = None,
    min_strength: float | None = None,
    **_: object,
) -> dict[str, Any]:
    if not node:
        return {"error": "node is required"}
    return _traverse_edges_impl(
        node=node,
        hops=hops or 1,
        edge_type=edge_type,
        min_strength=min_strength if min_strength is not None else 0.0,
    )


def _op_edge_retire(
    edge_id: int | None = None,
    valid_until: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if edge_id is None:
        return {"error": "edge_id is required"}
    body: dict[str, Any] = {}
    if valid_until is not None:
        body["valid_until"] = valid_until
    return _retire_edge_impl(edge_id, body)


def _op_edge_update(
    edge_id: int | None = None,
    strength: float | None = None,
    context: str | None = None,
    prompt: str | None = None,
    metadata: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if edge_id is None:
        return {"error": "edge_id is required"}
    body: dict[str, Any] = {}
    for key, val in (
        ("strength", strength),
        ("context", context),
        ("prompt", prompt),
        ("metadata", metadata),
    ):
        if val is not None:
            body[key] = val
    return _update_edge_impl(edge_id, body)


def _op_edge_types(**_: object) -> Any:
    return _list_edge_types_impl()


def _op_impact(
    entity_id: str | None = None,
    depth: int | None = None,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    return impact_analysis(entity_id=entity_id, depth=depth or 2)
