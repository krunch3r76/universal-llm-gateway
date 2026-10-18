"""Pre-validation and write helpers for `related` session edges on `friction()` and `assert()`.

Validates a caller-supplied `related` edge list before any assertion row is written so
deterministic schema, grammar, edge-type, or dangling-target errors return HTTP 422 without
leaving an orphan assertion behind. Post-create edge writes isolate per-edge failures into
`related_errors` while recording `mcp.cortex.edge.created` telemetry on every created edge.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from ..routes.edges import _create_edge_impl, _endpoint_resolves
from ._shared import _SESSION_ID_RE, record
from ._write_validation import (
    HTTP_422,
    field_error,
    is_missing,
    validation_error_response,
)
from .ops_edges import _session_id_grammar_error

RELATED_MAX = 20
DEFAULT_EDGE_TYPE = "relates_to"
DEFAULT_STRENGTH = 0.8


@dataclass(frozen=True, slots=True)
class PreparedRelatedEdge:
    """Normalized and pre-validated session edge specification ready for post-assert insert.

    Created by `prepare_related_edges` after verifying edge type membership and endpoint
    existence, then consumed by `write_related_edges` once the source assertion ID is known.
    """

    to_node: str
    edge_type: str
    strength: float
    context: str | None = None


def _normalize_target_node(item: dict[str, Any], index: int) -> tuple[str | None, dict[str, Any] | None]:
    has_aid = not is_missing(item.get("assertion_id"))
    has_to = not is_missing(item.get("to_node"))
    if has_aid == has_to:
        return None, field_error(
            f"related[{index}]",
            "each related item must supply exactly one of assertion_id or to_node",
            code="invalid_related_target",
        )
    if has_aid:
        raw = str(item["assertion_id"]).strip()
        for prefix in ("assertion:", "a:"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
                break
        try:
            aid = int(raw)
        except ValueError:
            return None, field_error(
                f"related[{index}].assertion_id",
                f"invalid assertion_id {item['assertion_id']!r}; expected integer or 'assertion:N'",
                code="invalid_assertion_id",
            )
        return f"assertion:{aid}", None
    to_node = str(item["to_node"]).strip()
    if to_node.startswith("a:") and to_node[2:].isdigit():
        to_node = f"assertion:{to_node[2:]}"
    return to_node, None


def prepare_related_edges(
    conn: sqlite3.Connection,
    related: object,
    *,
    session_id: str | None,
    agent: str | None,
) -> tuple[list[PreparedRelatedEdge], None] | tuple[None, dict[str, Any]]:
    """Validate a `related` edge list before any assertion row is written to the database.

    Checks list cardinality, `session_id` grammar, non-blank `agent`, target node resolution,
    and `session_edge_types` membership; returns `(prepared_edges, None)` or `(None, error_dict)`.
    """
    if not isinstance(related, list) or len(related) > RELATED_MAX:
        return None, validation_error_response(
            [
                field_error(
                    "related",
                    f"related must be a list of at most {RELATED_MAX} edge specs",
                    code="invalid_related",
                )
            ]
        )
    if not related:
        return [], None
    if is_missing(agent):
        return None, validation_error_response(
            [
                field_error(
                    "agent",
                    "agent is required when related is given",
                    code="missing_agent",
                )
            ]
        )
    if is_missing(session_id) or not _SESSION_ID_RE.match(str(session_id)):
        return None, _session_id_grammar_error(str(session_id or ""), agent=agent)

    valid_types = {
        r["type"]
        for r in conn.execute("SELECT type FROM session_edge_types ORDER BY type").fetchall()
    }
    prepared: list[PreparedRelatedEdge] = []
    missing_nodes: list[str] = []

    for idx, item in enumerate(related):
        if not isinstance(item, dict):
            return None, validation_error_response(
                [
                    field_error(
                        f"related[{idx}]",
                        "each related item must be an object",
                        code="invalid_related_item",
                    )
                ]
            )
        to_node, target_err = _normalize_target_node(item, idx)
        if target_err is not None or to_node is None:
            return None, validation_error_response([target_err])  # type: ignore[list-item]

        edge_type = str(item.get("edge_type") or DEFAULT_EDGE_TYPE).strip()
        if edge_type not in valid_types:
            return None, {
                "error": {
                    "error": f"Unknown edge_type: {edge_type!r}",
                    "field": f"related[{idx}].edge_type",
                    "valid_types": sorted(valid_types),
                    "hint": "Use one of valid_types above, or cortex(tool='edge_types') for descriptions and directionality.",
                },
                "status_code": HTTP_422,
            }

        raw_strength = item.get("strength", DEFAULT_STRENGTH)
        try:
            strength = float(raw_strength)
        except (TypeError, ValueError):
            return None, validation_error_response(
                [
                    field_error(
                        f"related[{idx}].strength",
                        "strength must be a float between 0.0 and 1.0",
                        code="invalid_strength",
                    )
                ]
            )
        if not (0.0 <= strength <= 1.0):
            return None, validation_error_response(
                [
                    field_error(
                        f"related[{idx}].strength",
                        "strength must be between 0.0 and 1.0",
                        code="invalid_strength",
                    )
                ]
            )

        context_raw = item.get("context")
        context = str(context_raw) if context_raw is not None else None

        if not _endpoint_resolves(conn, to_node):
            missing_nodes.append(to_node)
        else:
            prepared.append(
                PreparedRelatedEdge(
                    to_node=to_node,
                    edge_type=edge_type,
                    strength=strength,
                    context=context,
                )
            )

    if missing_nodes:
        return None, {
            "error": {
                "reason": "dangling_edge",
                "error": f"Edge endpoints not found: {missing_nodes}",
                "missing_nodes": missing_nodes,
                "hint": "Verify target assertion IDs or create the target entity before linking.",
            },
            "status_code": HTTP_422,
        }

    return prepared, None


def write_related_edges(
    assertion_id: int,
    prepared: list[PreparedRelatedEdge],
    *,
    session_id: str,
    agent: str,
    seeded_by: str | None = None,
) -> dict[str, Any]:
    """Write pre-validated session edges from `assertion:{assertion_id}` after assertion creation.

    Calls `_create_edge_impl` per item and emits `mcp.cortex.edge.created` for each success;
    captures any runtime `HTTPException` in `related_errors` so remaining edges still persist.
    """
    from_node = f"assertion:{assertion_id}"
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in prepared:
        body: dict[str, Any] = {
            "session_id": session_id,
            "agent": agent,
            "from_node": from_node,
            "to_node": item.to_node,
            "edge_type": item.edge_type,
            "strength": item.strength,
        }
        if item.context is not None:
            body["context"] = item.context
        if seeded_by is not None:
            body["seeded_by"] = seeded_by
        try:
            edge = _create_edge_impl(body)
        except HTTPException as exc:
            errors.append(
                {
                    "to_node": item.to_node,
                    "edge_type": item.edge_type,
                    "error": exc.detail,
                }
            )
            continue
        created.append(edge)
        record(
            "mcp.cortex.edge.created",
            session_id=session_id,
            edge_type=item.edge_type,
            from_node=from_node,
            to_node=item.to_node,
        )
    out: dict[str, Any] = {"related_edges": created}
    if errors:
        out["related_errors"] = errors
    return out
