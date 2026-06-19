"""Cortex dispatch op registry — single source of truth.

Exports:
  _OPS          — dict[op_name -> handler callable]
  execute_op    — dispatch entry: (tool, arguments) -> result dict
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ._shared import record
from .ops_assertions import (
    _op_activate,
    _op_analyze_impact,
    _op_assert,
    _op_assertion_get,
    _op_assertion_state,
    _op_assertion_update,
    _op_assertions,
    _op_friction,
    _op_friction_close,
    _op_frictions,
    _op_observe,
    _op_review_queue,
    _op_search,
    _op_supersede,
)
from .ops_audit import _op_audit
from .ops_bulk import _op_entities_bulk_upsert, _op_relationships_bulk_upsert
from .ops_composites import _op_register_skill_substrate
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
    _op_entities_by_content_hash,
    _op_entity_create,
    _op_entity_get,
    _op_entity_merge,
    _op_entity_rekey,
    _op_entity_update,
)
from .ops_journals import (
    _op_deadline_resolve,
    _op_deadlines,
    _op_journal_read,
    _op_journal_write,
)
from .ops_misc import (
    _op_resolve,
    _op_resolve_assertion_chunk,
    _op_stats,
    _op_surface_forms,
    _op_tag_assign,
    _op_tag_list,
    _op_tag_resolve,
    _op_thread_sidecar_write,
    _op_pinned_deliverable_write,
)
from .ops_reflective import (
    _op_rj_consolidate,
    _op_rj_link,
    _op_rj_list,
    _op_rj_read,
    _op_rj_write,
)
from .ops_relationships import (
    _op_relationship_create,
    _op_relationship_delete,
    _op_relationship_update,
    _op_relationships,
)
from .ops_review import _op_case_audit, _op_fill_gaps, _op_session_audit
from .ops_implement_ready_preflight import _op_implement_ready_preflight
from .ops_session_close import (
    _op_session_close,
    _op_session_close_preflight,
    _op_session_handoff_upsert,
)
from .ops_subgraph import _op_render_subgraph, _op_walk_subgraph
from .ops_todos import (
    _op_todo_audit,
    _op_todo_candidates,
    _op_todo_close_sidecar,
    _op_todo_distill_implement_gate,
)
from .ops_transcript_assembly import _op_assemble_transcript
from .workflow_hints import (
    _CORTEX_FORMAT_HINT,
    _CORTEX_HALLUCINATED_TOOLS,
    _CORTEX_LARGE_PAYLOAD_OPS,
    _CORTEX_OFFLOAD_HINT,
    _FRICTION_HINT,
    _WORKFLOW_HINTS,
    _enrich_entity_completeness,
    _parse_cortex_arguments,
    attach_session_close_protocol,
)

logger = get_logger("cortex-api.dispatch_ops")


_OPS: dict[str, Any] = {
    "entities": _op_entities,
    "entities_by_content_hash": _op_entities_by_content_hash,
    "entity_get": _op_entity_get,
    "entity_create": _op_entity_create,
    "entities_bulk_upsert": _op_entities_bulk_upsert,
    "entity_update": _op_entity_update,
    "entity_rekey": _op_entity_rekey,
    "entity_merge": _op_entity_merge,
    "assertion_state": _op_assertion_state,
    "assertions": _op_assertions,
    "assert": _op_assert,
    "observe": _op_observe,
    "friction": _op_friction,
    "friction_close": _op_friction_close,
    "frictions": _op_frictions,
    "assertion_get": _op_assertion_get,
    "assertion_update": _op_assertion_update,
    "supersede": _op_supersede,
    "resolve_assertion_chunk": _op_resolve_assertion_chunk,
    "relationships": _op_relationships,
    "relationship_create": _op_relationship_create,
    "relationships_bulk_upsert": _op_relationships_bulk_upsert,
    "relationship_delete": _op_relationship_delete,
    "relationship_update": _op_relationship_update,
    "stats": _op_stats,
    "surface_forms": _op_surface_forms,
    "deadlines": _op_deadlines,
    "deadline_resolve": _op_deadline_resolve,
    "journal_read": _op_journal_read,
    "journal_write": _op_journal_write,
    "session_close": _op_session_close,
    "session_close_preflight": _op_session_close_preflight,
    "implement_ready_preflight": _op_implement_ready_preflight,
    "session_handoff_upsert": _op_session_handoff_upsert,
    "assemble_transcript": _op_assemble_transcript,
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
    "thread_sidecar_write": _op_thread_sidecar_write,
    "pinned_deliverable_write": _op_pinned_deliverable_write,
    "todo_close_sidecar": _op_todo_close_sidecar,
    "todo_distill_implement_gate": _op_todo_distill_implement_gate,
    "register_skill_substrate": _op_register_skill_substrate,
    "audit": _op_audit,
    "session_audit": _op_session_audit,
    "case_audit": _op_case_audit,
    "fill_gaps": _op_fill_gaps,
    "rj_write": _op_rj_write,
    "rj_read": _op_rj_read,
    "rj_list": _op_rj_list,
    "rj_link": _op_rj_link,
    "rj_consolidate": _op_rj_consolidate,
    "render_subgraph": _op_render_subgraph,
    "walk_subgraph": _op_walk_subgraph,
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
        error = _CORTEX_FORMAT_HINT
        # Mirror the mcp-server dispatch sites: a failed *string* parse is almost
        # always an escaping problem on a large quote-heavy payload, so point the
        # caller at the file-path / CLI offload instead of leaving them to
        # re-escape by hand. See decision:dispatch-arguments-string-wire-form.
        if isinstance(arguments, str) and tool in _CORTEX_LARGE_PAYLOAD_OPS:
            error += _CORTEX_OFFLOAD_HINT
        return {
            "error": error,
            "format_example": (
                f'cortex(tool="{tool}", arguments=\'{{"entity_id": "type:slug"}}\')'
            ),
        }

    record("mcp.cortex.dispatch", tool=tool)
    result = handler(**parsed)
    if not isinstance(result, dict):
        return result
    attach_session_close_protocol(result, tool)
    if "error" in result:
        result["_hint"] = _FRICTION_HINT
        return result
    # Handler-set _next takes precedence — it carries per-call detail
    # (which warning categories fired, which suggestion is most relevant).
    # Static workflow hints apply only when the handler didn't write one.
    hint = _WORKFLOW_HINTS.get(tool)
    if hint and "_next" not in result:
        result["_next"] = hint
    if tool == "entity_get" and result.get("intent") != "card":
        # Card v0 (§6.3) has its own bounded shape (top_k_assertions /
        # section_manifest); the EntityDetail-shaped completeness hint
        # would misreport "no assertions" against the projection.
        _enrich_entity_completeness(result)
    return result


__all__ = ["_OPS", "execute_op"]
