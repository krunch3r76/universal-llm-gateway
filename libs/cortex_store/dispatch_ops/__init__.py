"""Cortex dispatch op registry — single source of truth.

Exports:
  _OPS          — dict[op_name -> handler callable]
  execute_op    — dispatch entry: (tool, arguments) -> result dict
"""

from __future__ import annotations

import logging
from typing import Any

from ._shared import record
from .ops_assertions import (
    _op_activate,
    _op_analyze_impact,
    _op_assert,
    _op_assert_from_chunk,
    _op_assertion_update,
    _op_assertions,
    _op_friction,
    _op_observe,
    _op_review_queue,
    _op_search,
    _op_supersede,
)
from .ops_edges import (
    _op_edge_create,
    _op_edge_retire,
    _op_edge_traverse,
    _op_edge_types,
    _op_edges,
    _op_impact,
)
from .ops_entities import (
    _op_entities,
    _op_entity_create,
    _op_entity_get,
    _op_entity_update,
)
from .ops_journals import (
    _op_deadline_resolve,
    _op_deadlines,
    _op_journal_read,
    _op_journal_write,
    _op_session_close,
)
from .ops_misc import (
    _op_ingest_document,
    _op_resolve,
    _op_stats,
    _op_surface_forms,
    _op_tag_assign,
    _op_tag_list,
    _op_tag_resolve,
)
from .ops_reflective import (
    _op_rj_consolidate,
    _op_rj_link,
    _op_rj_list,
    _op_rj_read,
    _op_rj_write,
)
from .ops_relationships import _op_relationship_create, _op_relationships
from .ops_todos import _op_todo_audit, _op_todo_candidates
from .workflow_hints import (
    _CORTEX_FORMAT_HINT,
    _CORTEX_HALLUCINATED_TOOLS,
    _FRICTION_HINT,
    _WORKFLOW_HINTS,
    _enrich_entity_completeness,
    _parse_cortex_arguments,
)

logger = logging.getLogger("cortex-api.dispatch_ops")


_OPS: dict[str, Any] = {
    "entities": _op_entities,
    "entity_get": _op_entity_get,
    "entity_create": _op_entity_create,
    "entity_update": _op_entity_update,
    "assertions": _op_assertions,
    "assert": _op_assert,
    "observe": _op_observe,
    "friction": _op_friction,
    "assertion_update": _op_assertion_update,
    "supersede": _op_supersede,
    "ingest_document": _op_ingest_document,
    "assert_from_chunk": _op_assert_from_chunk,
    "relationships": _op_relationships,
    "relationship_create": _op_relationship_create,
    "stats": _op_stats,
    "surface_forms": _op_surface_forms,
    "deadlines": _op_deadlines,
    "deadline_resolve": _op_deadline_resolve,
    "journal_read": _op_journal_read,
    "journal_write": _op_journal_write,
    "session_close": _op_session_close,
    "review_queue": _op_review_queue,
    "edge_create": _op_edge_create,
    "edges": _op_edges,
    "edge_traverse": _op_edge_traverse,
    "edge_retire": _op_edge_retire,
    "edge_types": _op_edge_types,
    "impact": _op_impact,
    "activate": _op_activate,
    "resolve": _op_resolve,
    "search": _op_search,
    "analyze_impact": _op_analyze_impact,
    "tag_assign": _op_tag_assign,
    "tag_list": _op_tag_list,
    "tag_resolve": _op_tag_resolve,
    "todo_candidates": _op_todo_candidates,
    "todo_audit": _op_todo_audit,
    "rj_write": _op_rj_write,
    "rj_read": _op_rj_read,
    "rj_list": _op_rj_list,
    "rj_link": _op_rj_link,
    "rj_consolidate": _op_rj_consolidate,
}


def execute_op(tool: str, arguments: object) -> Any:
    """Dispatch a cortex op. Handles unknown-tool suggestions, arg parsing,
    workflow hints, and entity completeness enrichment.
    """
    handler = _OPS.get(tool)
    if handler is None:
        suggestion = _CORTEX_HALLUCINATED_TOOLS.get(tool)
        hint = f"Did you mean {suggestion!r}?" if suggestion else None
        return {
            "error": f"Unknown cortex tool {tool!r}. Available: {sorted(_OPS)}",
            **({"hint": hint} if hint else {}),
            "format_example": (
                'cortex(tool="entity_get", arguments=\'{"entity_id": "type:slug"}\')'
            ),
        }

    parsed = _parse_cortex_arguments(arguments, tool)
    if parsed is None:
        return {
            "error": _CORTEX_FORMAT_HINT,
            "format_example": (
                f'cortex(tool="{tool}", arguments=\'{{"entity_id": "type:slug"}}\')'
            ),
        }

    record("mcp.cortex.dispatch", tool=tool)
    result = handler(**parsed)
    if not isinstance(result, dict):
        return result
    if "error" in result:
        result["_hint"] = _FRICTION_HINT
        return result
    hint = _WORKFLOW_HINTS.get(tool)
    if hint:
        result["_next"] = hint
    if tool == "entity_get":
        _enrich_entity_completeness(result)
    return result


__all__ = ["_OPS", "execute_op"]
