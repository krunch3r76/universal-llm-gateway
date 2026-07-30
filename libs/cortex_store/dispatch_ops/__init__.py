"""Cortex dispatch op registry — single source of truth.

Exports:
  _OPS          — Mapping[op_name -> handler callable]. Handlers are imported
                  and memoized on first lookup (see ``_LazyOpRegistry``) so that
                  importing this package does NOT eagerly import every ``ops_*``
                  submodule. The eager imports formed an import cycle:
                  ``session_close_validation`` -> ``dispatch_ops._shared`` ->
                  (``dispatch_ops`` package __init__) -> ``ops_journals`` /
                  ``ops_session_close`` -> ... -> ``session_close_validation``
                  (partially initialized) -> ImportError. Resolving handlers on
                  demand keeps package import leaf-safe.
  execute_op    — dispatch entry: (tool, arguments) -> result dict
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from universal_logging import get_logger

from ..skill_hint_projection import get_skill_hint
from ._shared import record
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

_PKG = __name__  # "cortex_store.dispatch_ops" — base for lazy submodule import

# Params hidden from MCP doc surfaces (generator deny-set). raw_id/resolve_aliases
# are intentionally agent-facing and must NOT appear here — panel adjudication
# agent-bus:3903/3904/3905, assertion 21735.
_INTERNAL_PARAMS: frozenset[str] = frozenset(
    {"include_compaction_pointers", "content_hash", "status", "emit"}
)
_DEPRECATED_PARAM_NAMES: frozenset[str] = frozenset({"service"})


# op-name -> "ops_submodule:attribute". The handler callable is resolved with
# importlib on first lookup and memoized (see ``_LazyOpRegistry``). Keep this in
# sync with the ``ops_*`` modules; every value is "<module>:_op_<name>".
_OP_SPECS: dict[str, str] = {
    "entities": "ops_entities:_op_entities",
    "entities_by_content_hash": "ops_entities:_op_entities_by_content_hash",
    "entity_get": "ops_entities:_op_entity_get",
    "entity_create": "ops_entities:_op_entity_create",
    "entities_bulk_upsert": "ops_bulk:_op_entities_bulk_upsert",
    "entity_update": "ops_entities:_op_entity_update",
    "entity_rekey": "ops_entities:_op_entity_rekey",
    "entity_retype": "ops_entities:_op_entity_retype",
    "entity_merge": "ops_entities:_op_entity_merge",
    "assertion_state": "ops_assertions:_op_assertion_state",
    "assertions": "ops_assertions:_op_assertions",
    "assert": "ops_assertions:_op_assert",
    "observe": "ops_assertions:_op_observe",
    "friction": "ops_assertions:_op_friction",
    "friction_close": "ops_assertions:_op_friction_close",
    "frictions": "ops_assertions:_op_frictions",
    "assertion_get": "ops_assertions:_op_assertion_get",
    "assertion_update": "ops_assertions:_op_assertion_update",
    "supersede": "ops_assertions:_op_supersede",
    "resolve_assertion_chunk": "adapters.rag:_op_resolve_assertion_chunk",
    "relationships": "ops_relationships:_op_relationships",
    "relationship_create": "ops_relationships:_op_relationship_create",
    "relationships_bulk_upsert": "ops_bulk:_op_relationships_bulk_upsert",
    "relationship_delete": "ops_relationships:_op_relationship_delete",
    "relationship_update": "ops_relationships:_op_relationship_update",
    "stats": "ops_misc:_op_stats",
    "surface_forms": "ops_misc:_op_surface_forms",
    "deadlines": "ops_journals:_op_deadlines",
    "deadline_resolve": "ops_journals:_op_deadline_resolve",
    "digest": "ops_digest:_op_digest",
    "staging_list": "ops_staging:_op_staging_list",
    "staging_batch_approve": "ops_staging:_op_staging_batch_approve",
    "staging_approve": "ops_staging:_op_staging_approve",
    "staging_reject": "ops_staging:_op_staging_reject",
    "journal_read": "ops_journals:_op_journal_read",
    "session_close": "ops_session_close:_op_session_close",
    "session_close_preflight": "ops_session_close:_op_session_close_preflight",
    "implement_ready_preflight": (
        "adapters.admission:_op_implement_ready_preflight"
    ),
    "doc_template": "adapters.admission:_op_doc_template",
    "doc_validate": "adapters.admission:_op_doc_validate",
    "session_handoff_upsert": "ops_session_close:_op_session_handoff_upsert",
    "assemble_transcript": "ops_transcript_assembly:_op_assemble_transcript",
    "review_queue": "ops_assertions:_op_review_queue",
    "edge_create": "ops_edges:_op_edge_create",
    "edges": "ops_edges:_op_edges",
    "edge_traverse": "ops_edges:_op_edge_traverse",
    "edge_retire": "ops_edges:_op_edge_retire",
    "edge_update": "ops_edges:_op_edge_update",
    "edge_types": "ops_edges:_op_edge_types",
    "impact": "ops_edges:_op_impact",
    "graph_reach": "ops_edges:_op_impact",
    "activate": "ops_assertions:_op_activate",
    "resolve": "ops_misc:_op_resolve",
    "search": "ops_assertions:_op_search",
    "analyze_impact": "ops_assertions:_op_analyze_impact",
    "claim_alignment": "ops_assertions:_op_analyze_impact",
    "tag_assign": "ops_misc:_op_tag_assign",
    "tag_list": "ops_misc:_op_tag_list",
    "tag_resolve": "ops_misc:_op_tag_resolve",
    "todo_candidates": "ops_todos:_op_todo_candidates",
    "todo_audit": "ops_todos:_op_todo_audit",
    "thread_sidecar_write": "ops_misc:_op_thread_sidecar_write",
    "recon_sidecar_write": "ops_misc:_op_recon_sidecar_write",
    "pinned_deliverable_write": "ops_misc:_op_pinned_deliverable_write",
    "todo_close_sidecar": "ops_todos:_op_todo_close_sidecar",
    "todo_distill_implement_gate": "ops_todos:_op_todo_distill_implement_gate",
    "endeavor_write_row": "ops_endeavor_birth:_op_endeavor_write_row",
    "endeavor_dispose_row": "ops_endeavor_birth:_op_endeavor_dispose_row",
    "endeavor_lock_ready": "ops_endeavor_birth:_op_endeavor_lock_ready",
    "endeavor_repair_t1": "ops_endeavor_birth:_op_endeavor_repair_t1",
    "register_skill_substrate": "ops_composites:_op_register_skill_substrate",
    "audit": "ops_audit:_op_audit",
    "session_audit": "ops_review:_op_session_audit",
    "case_audit": "ops_review:_op_case_audit",
    "fill_gaps": "ops_review:_op_fill_gaps",
    "rj_write": "ops_reflective:_op_rj_write",
    "rj_read": "ops_reflective:_op_rj_read",
    "rj_list": "ops_reflective:_op_rj_list",
    "rj_link": "ops_reflective:_op_rj_link",
    "rj_consolidate": "ops_reflective:_op_rj_consolidate",
    "render_subgraph": "ops_subgraph:_op_render_subgraph",
    "walk_subgraph": "ops_subgraph:_op_walk_subgraph",
    "prose_fact_scan": "ops_prose_fact_scan:_op_prose_fact_scan",
    "view_render": "ops_views:_op_view_render",
}


class _LazyOpRegistry(Mapping[str, Callable[..., Any]]):
    """op-name -> handler mapping that imports handlers lazily on first use.

    Preserves the historical ``dict`` contract relied on by ``execute_op`` and
    the regression suite: ``in`` membership and ``sorted()`` never trigger an
    import, ``.get()`` returns the handler or ``None``, and indexing is
    identity-stable (a given op always resolves to the one module-level function
    object, so ``_OPS["assertion_state"] is _op_assertion_state``).
    """

    __slots__ = ("_specs", "_cache")

    def __init__(self, specs: dict[str, str]) -> None:
        self._specs = specs
        self._cache: dict[str, Callable[..., Any]] = {}

    def __getitem__(self, key: str) -> Callable[..., Any]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            spec = self._specs[key]
        except KeyError:
            raise KeyError(key) from None
        module_name, _, attr = spec.partition(":")
        handler: Callable[..., Any] = getattr(
            importlib.import_module(f"{_PKG}.{module_name}"), attr
        )
        self._cache[key] = handler
        return handler

    def __contains__(self, key: object) -> bool:
        # Membership is answered from the spec table — never imports a handler.
        return key in self._specs

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)


_OPS: Mapping[str, Callable[..., Any]] = _LazyOpRegistry(_OP_SPECS)


def execute_op(
    tool: str,
    arguments: object,
    *,
    surface: str | None = None,
    seat: str | None = None,
    via_adapter: bool | None = None,
) -> Any:
    """Dispatch a cortex op. Handles unknown-tool suggestions, arg parsing,
    workflow hints, and entity completeness enrichment.

    Telemetry kwargs (surface, seat, via_adapter) are populated by the MCP
    relay pass-through for per-op × per-seat ``mcp.cortex.dispatch`` events.
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

    dispatch_telemetry: dict[str, Any] = {"tool": tool}
    if surface:
        dispatch_telemetry["surface"] = surface
    if seat:
        dispatch_telemetry["seat"] = seat
    if via_adapter is not None:
        dispatch_telemetry["via_adapter"] = via_adapter
    if tool == "entity_get":
        dispatch_telemetry["intent"] = parsed.get("intent") or "full"
    record("mcp.cortex.dispatch", **dispatch_telemetry)
    result = handler(**parsed)
    if not isinstance(result, dict):
        return result
    attach_session_close_protocol(result, tool)
    if "error" in result:
        result["_hint"] = _FRICTION_HINT
        code = result.get("code")
        if isinstance(code, str) and code.strip():
            skill_hint = get_skill_hint(code.strip())
            if skill_hint is not None:
                result["skill_hint"] = skill_hint
        return result
    is_batch_entity_get = tool == "entity_get" and "items" in result
    # Handler-set _next takes precedence — it carries per-call detail
    # (which warning categories fired, which suggestion is most relevant).
    # Static workflow hints apply only when the handler didn't write one.
    hint = _WORKFLOW_HINTS.get(tool)
    if hint and "_next" not in result:
        result["_next"] = hint
    if tool == "entity_get" and not is_batch_entity_get:
        from ..terminal_facts import append_terminal_facts_next_hint

        append_terminal_facts_next_hint(result)
    if tool == "entity_get" and not is_batch_entity_get and result.get("intent") != "card":
        # Card v0 (§6.3) has its own bounded shape (top_k_assertions /
        # section_manifest); the EntityDetail-shaped completeness hint
        # would misreport "no assertions" against the projection.
        _enrich_entity_completeness(result)
    if tool == "entity_get" and isinstance(result, dict) and not is_batch_entity_get:
        try:
            _intent = result.get("intent") or (parsed.get("intent") or "full")
            if "assertions" in result:
                _active = len(result.get("assertions") or [])
            else:
                _active = (result.get("assertion_counts") or {}).get("active")
            _superseded = (result.get("superseded_breadcrumb") or {}).get("count")
            if _superseded is None:
                _superseded = (result.get("assertion_counts") or {}).get("superseded")
            record(
                "mcp.cortex.entity_get.served",
                intent=_intent,
                entity_id=result.get("id"),
                active_count=_active,
                superseded_count=_superseded,
            )
        except Exception:
            pass
    return result


__all__ = [
    "_DEPRECATED_PARAM_NAMES",
    "_INTERNAL_PARAMS",
    "_OP_SPECS",
    "_OPS",
    "execute_op",
]
