"""Agent-UX hints for cortex dispatch responses.

Workflow-next pointers, friction logging hint, hallucinated-tool suggestions,
and entity completeness enrichment. Injected into successful and failed
dispatch responses by the /dispatch router.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

logger = logging.getLogger("cortex-api.dispatch_ops")


_WORKFLOW_HINTS: dict[str, str] = {
    "entity_create": (
        "next: assert (seed claims with evidence_uris) "
        "→ relationship_create (wire edges to related entities) "
        "→ entity_get (verify full graph)"
    ),
    "assert": (
        "next: relationship_create if this claim connects two entities; "
        "entity_get to verify the assertion appears on the entity. "
        "If validation_warnings present, assertion was routed to staged — "
        "add reasoning_summary or chunk_id to graduate."
    ),
    "assert_from_chunk": (
        "next: relationship_create if connecting entities; entity_get to verify"
    ),
    "relationship_create": (
        "next: entity_get on source_id or target_id to verify the full graph "
        "(entity + assertions + relationships). Pass include_edges=true to also "
        "see reasoning edges. Tip: pass session_id and agent for provenance."
    ),
    "entity_update": "next: entity_get to confirm the updated state is reflected",
    "supersede": (
        "next: entity_get to confirm the new assertion is visible "
        "and the old one is marked superseded; "
        "tag_assign to pin the new assertion as 'current' if it is the canonical state"
    ),
    "ingest_document": (
        "next: assert_from_chunk to pin specific claims to chunk IDs; "
        "entity_get to verify"
    ),
    "journal_write": (
        "DEPRECATED: Use session_close instead. session_close atomically writes "
        "the transcript file, creates the entity, journal row, and continues edge "
        "in one call — preventing the stub-only failures that journal_write allows."
    ),
    "session_close": (
        "next: seed content assertions on relevant entities (decisions, observations); "
        "post to agent bus thread 480 with session debrief; "
        "entity_get on transcript_entity_id to confirm the full record. "
        "Review staged_assertions from review_queue (F2) — add reasoning_summary or chunk_id to graduate."
    ),
    "search": (
        "next: extract entity_ids from results → activate (for structurally "
        "connected assertions the query wouldn't find directly); "
        "before writing a new assertion, call analyze_impact to check for contradictions"
    ),
    "rj_write": (
        "next: review suggested_links and accept relevant ones via rj_link; "
        "check if this entry contradicts or revises earlier entries"
    ),
    "rj_consolidate": (
        "next: rj_list to verify the consolidation appears; "
        "rj_link to connect any entries the consolidation missed"
    ),
    "edge_create": (
        "next: entity_get with include_edges=true on from_node or to_node "
        "to verify the edge is visible in the reasoning graph"
    ),
    "entity_get": (
        "tip: pass include_edges=true to also see reasoning edges "
        "(session-attributed cognitive connections). "
        "Relationships are structural links; edges are reasoning links."
    ),
    "edges": (
        "tip: to see edges in entity context, use entity_get with include_edges=true. "
        "To traverse multi-hop, use edge_traverse."
    ),
    "relationships": (
        "tip: to create typed structural links, use relationship_create with "
        "session_id + agent for provenance. Available types: supplement_to, "
        "filed_against, respondent_in, evidence_for, recipient_of, issued_by, "
        "owns, references, parent_of/child_of, sibling_of, related_to, and more."
    ),
    "relationship_delete": (
        "next: relationship_create if recreating with the correct direction or type; "
        "entity_get on the formerly linked entities to confirm the link no longer appears"
    ),
    "relationship_update": (
        "next: entity_get on source or target entity to confirm the update is reflected. "
        "To fix relationship direction or type, use relationship_delete then relationship_create."
    ),
    "activate": (
        "next: review the spreading activation results for structurally connected "
        "assertions the original search wouldn't find directly"
    ),
}

_FRICTION_HINT = (
    "If this failure was unexpected, log friction: "
    'cortex(tool="friction", arguments=\'{"service": "...", '
    '"category": "tool_error", "note": "...", "agent": "..."}\')'
)

_CORTEX_FORMAT_HINT = (
    "arguments must be a JSON-encoded object string (the MCP tool schema "
    "declares type=string). "
    'Example: cortex(tool="entity_get", arguments=\'{"entity_id": "service:mcp-server"}\')'
)

_CORTEX_HALLUCINATED_TOOLS: dict[str, str] = {
    "search_assertions": "search",
    "search_entities": "entities",
    "get_entity": "entity_get",
    "entity_search": "search",
    "assert_entity": "assert",
    "create_entity": "entity_create",
    "update_entity": "entity_update",
    "create_relationship": "relationship_create",
    "list_relationships": "relationships",
    "get_relationships": "relationships",
    "delete_relationship": "relationship_delete",
    "remove_relationship": "relationship_delete",
    "update_relationship": "relationship_update",
    "patch_relationship": "relationship_update",
    "create_edge": "edge_create",
    "list_edges": "edges",
    "get_edges": "edges",
    "traverse": "edge_traverse",
    "list_edge_types": "edge_types",
    "get_edge_types": "edge_types",
    "assertion_get": "assertions",
}


def _parse_cortex_arguments(arguments: object, tool: str) -> dict[str, Any] | None:
    """Return parsed arguments dict, or None on parse failure.

    Accepts object or JSON-string form; xAI remote-MCP emits objects, legacy
    callers emit JSON strings. Either is accepted silently.
    """
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            result = _json.loads(arguments)
        except _json.JSONDecodeError as exc:
            logger.warning("cortex %r: arguments JSON parse failed: %s", tool, exc)
            return None
        if not isinstance(result, dict):
            return None
        return result
    return None


def _enrich_entity_completeness(result: dict[str, Any]) -> None:
    """Add a _completeness hint if the entity is thin. Mutates result in place."""
    gaps: list[str] = []
    assertions = result.get("assertions")
    if isinstance(assertions, list) and len(assertions) == 0:
        gaps.append("no assertions — seed claims via assert")
    relationships = result.get("relationships")
    if isinstance(relationships, list) and len(relationships) == 0:
        gaps.append("no relationships — wire edges via relationship_create")
    reasoning_edges = result.get("reasoning_edges")
    if isinstance(reasoning_edges, list) and len(reasoning_edges) == 0:
        gaps.append(
            "no reasoning edges visible — pass include_edges=true to surface them, "
            "or seed via edge_create"
        )
    desc = result.get("description") or ""
    if len(desc) < 50:
        gaps.append("thin description (<50 chars) — enrich via entity_update")
    if gaps:
        result["_completeness"] = "; ".join(gaps)
