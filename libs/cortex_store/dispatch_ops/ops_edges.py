"""Reasoning edge ops — create, list, traverse, retire, update, types, and impact.

Exposes dispatch handlers for session-attributed reasoning edges, enforcing single-pass
required-field validation, session-ID grammar checks with seat hints, filter-key whitelisting,
and append-only provenance immutability.
"""

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
from ._shared import (
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
    record,
)
from ._write_validation import (
    HTTP_422,
    collect_missing_required,
    validation_error_response,
)

_EDGES_FILTERS = (
    "node",
    "from_node",
    "to_node",
    "edge_type",
    "edge_type_exclude",
    "agent",
    "session_id",
    "include_retired",
    "since_hours",
    "limit",
)
_EDGE_UPDATE_MUTABLE_FIELDS = ("strength", "context", "prompt", "metadata")
_EDGE_PROVENANCE_IMMUTABLE_FIELDS = frozenset(
    {"session_id", "agent", "seeded_by", "from_node", "to_node", "edge_type"}
)


def _session_id_grammar_error(
    session_id: str,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    """Refuse a `session_id` that does not match the session-grain identifier grammar.

    `session_edges.session_id` is the key `session_close` later joins against
    `session_journals.session_id` and `transcript:{session_id}`. When `agent` is supplied,
    attaches a `seat_hint` pointing to `cortex_brief(seat=<canonical>)` (friction a:39611).
    """
    out: dict[str, Any] = {
        "error": f"session_id {session_id!r} does not match the session grammar",
        "reason": "session_id_grammar",
        "expected": _SESSION_ID_RE_SOURCE,
        "examples": list(_SESSION_ID_EXAMPLES),
        "hint": (
            "Pass the id minted at session start (cortex_brief.session_id). A seat "
            "that has not booted uses the same derivation session_close applies "
            "when session_id is omitted: derive_session_id_from_jsonl_start on its "
            "own transcript JSONL (agent_seat.session_id grammar). Do not invent "
            "a label."
        ),
        "status_code": HTTP_422,
    }
    if agent and str(agent).strip():
        agent_str = str(agent).strip()
        if agent_str == "grok-bot" or agent_str.startswith("grok-bot-"):
            seat_slug = "grok-bot"
        else:
            from agent_seat.registry import normalize_bus_address

            seat_slug = normalize_bus_address(agent_str)
        out["seat_hint"] = f"mint via cortex_brief(seat='{seat_slug}')"
    return out


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
    missing = collect_missing_required(
        {
            "session_id": session_id,
            "agent": agent,
            "from_node": from_node,
            "to_node": to_node,
            "edge_type": edge_type,
        }
    )
    if missing:
        return validation_error_response(missing)
    assert session_id is not None
    if not _SESSION_ID_RE.match(session_id):
        return _session_id_grammar_error(session_id, agent=agent)
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
    node: str | None = None,
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    edge_type_exclude: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    include_retired: bool | None = None,
    since_hours: int | None = None,
    limit: int | None = None,
    **extra: object,
) -> dict[str, Any]:
    if extra:
        return {
            "error": {
                "error": "unknown_filter_keys",
                "unknown_keys": sorted(extra),
                "valid_filters": list(_EDGES_FILTERS),
                "hint": "edges filters by node (either endpoint), from_node or to_node — not node_id.",
            },
            "status_code": HTTP_422,
        }
    return _list_edges_impl(
        node=node,
        from_node=from_node,
        to_node=to_node,
        edge_type=edge_type,
        edge_type_exclude=edge_type_exclude,
        agent=agent,
        session_id=session_id,
        include_retired=bool(include_retired),
        since_hours=since_hours,
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
    **extra: object,
) -> dict[str, Any]:
    if edge_id is None:
        return {"error": "edge_id is required"}
    immutable_hit = sorted(_EDGE_PROVENANCE_IMMUTABLE_FIELDS & extra.keys())
    if immutable_hit:
        return {
            "error": {
                "error": "provenance_immutable",
                "fields": immutable_hit,
                "hint": (
                    "edge provenance and identity are append-only: "
                    "edge_update(metadata=…) to annotate, "
                    "PATCH /edges/{id}/retire (edge_retire) to retire, "
                    "edge_create under the correct session to re-seed."
                ),
            },
            "status_code": HTTP_422,
        }
    if extra:
        return {
            "error": {
                "error": "unknown_filter_keys",
                "unknown_keys": sorted(extra),
                "valid_fields": list(_EDGE_UPDATE_MUTABLE_FIELDS),
                "hint": "edge_update patches only strength, context, prompt, or metadata.",
            },
            "status_code": HTTP_422,
        }
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
