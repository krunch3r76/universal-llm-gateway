"""Agent-UX hints for cortex dispatch responses.

Workflow-next pointers, friction logging hint, hallucinated-tool suggestions,
and entity completeness enrichment. Injected into successful and failed
dispatch responses by the /dispatch router.
"""

from __future__ import annotations

import json as _json
from typing import Any

from ._shared import record

# Name-only skill refs — Use the `<slug>` skill; seat self-fetches; ¬ fs-read
# (friction 23128 / agent-bus:4888; agent-skills/ mirror retired by D3).
_SESSION_CLOSE_PROTOCOL = (
    "Before close: Use the `session-close-kernel` skill (seat-routed). "
    "Cursor: session-close.mdc + cortex(tool=session_close) — ¬ close(op=…), "
    "¬ retired cortex skill-mirror paths. "
    "Life/web primary: close(op=stage|draft|check|commit) then optional close(op=handoff). "
    "Transitional cortex session_close on web: also Use the `session-close-audit` skill. "
    "claude-web verbatim: Use the `web-transcript-preprocessing` skill before transcript_md. "
    "Load before close."
)

_SESSION_CLOSE_TOOLS = frozenset({"session_close", "session_close_preflight"})


def attach_session_close_protocol(result: dict[str, Any], tool: str) -> None:
    """Inject mandatory close-protocol pointer on session_close dispatch paths."""
    if tool in _SESSION_CLOSE_TOOLS:
        result["_protocol"] = _SESSION_CLOSE_PROTOCOL


_WORKFLOW_HINTS: dict[str, str] = {
    "entity_create": (
        "read-first: search/entities before create (write_discipline nudge is advisory); "
        "next: assert (seed claims with evidence_uris) "
        "→ relationship_create (wire child_of for leaf types) "
        "→ entity_get (verify full graph). "
        "Exact-slug 409 unchanged; post-create collision_warning surfaces semantic "
        "near-duplicates (advisory, non-blocking)"
    ),
    "entities_bulk_upsert": (
        "next: relationships_bulk_upsert to wire graph links in one atomic call; "
        "entity_get on representative IDs to verify"
    ),
    "assert": (
        "read-first: analyze_impact(entity_id, claim) before assert when unsure; "
        "write_discipline nudge (advisory) flags similar claims + hub bloat. "
        "next: relationship_create if this claim connects two entities; "
        "entity_get to verify the assertion appears on the entity. "
        "If validation_warnings present, the handler sets a per-call _next "
        "hint identifying which warning categories fired (staging vs auditor) "
        "and what to do."
    ),
    "resolve_assertion_chunk": (
        "next: entity_get on the assertion's entity_id to see the full context; "
        "use chunk.text to verify the claim is grounded in the source"
    ),
    "relationship_create": (
        "next: entity_get on source_id or target_id to verify the full graph "
        "(entity + assertions + relationships). Pass include_edges=true to also "
        "see reasoning edges. Tip: pass session_id and agent for provenance."
    ),
    "relationships_bulk_upsert": (
        "next: entity_get on representative source/target IDs to verify links; "
        "check resolved_aliases in items when aliases were used"
    ),
    "entity_update": (
        "next: entity_get(intent=card) to confirm the updated state is reflected"
    ),
    "supersede": (
        "next: entity_get(intent=card) to confirm the new assertion is visible "
        "and the old one is marked superseded; "
        "tag_assign to pin the new assertion as 'current' if it is the canonical state"
    ),
    "session_close": (
        "after close: seed content assertions on relevant entities (decisions, observations); "
        "post to agent bus thread 480 with session debrief; "
        "entity_get on transcript_entity_id to confirm the full record. "
        "Review staged_assertions from review_queue (F2) — to graduate, supersede each with the missing reasoning_summary or chunk_id (carryover preserves the rest; new row is the committed version). reasoning_summary is immutable post-creation per v1.3-additions §7.5.3."
    ),
    "session_close_preflight": (
        "on ok=true: proceed to session_close with same args. "
        "on ok=false: fix per skill `session-close-kernel` before retrying close. "
        "ID probe is NOT ID-only — supply session_id, agent, summary, and "
        "session_summary_md (placeholders OK); see session-close.mdc §0b."
    ),
    "session_handoff_upsert": (
        "next: entity_get on transcript_entity_id (or journal row via session_id) "
        "to verify the handoff_prompt is retrievable on explicit reference"
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
    "doc_template": (
        "next: fill every required section using the accepted-pattern hints; "
        "run doc_validate(text=…) or doc_validate(path=…) until status=pass; "
        "record attestation_tokens from the PASS response on the todo's "
        "implement-ready assertion before implement dispatch. "
        "Load: skill `implement-todo`"
    ),
    "doc_validate": (
        "on status=pass: cite attestation_tokens (doc_validate:pass, "
        "template_version, spec_sha256, skill_digest) on the implement-ready "
        "assertion; then distill files_expected + acceptance_criteria at Gate-2. "
        "on drifted_since_ready: refresh spec_sha256 on the assertion after spec edit. "
        "Read-only — does not trigger implement side-effect guard."
    ),
    "implement_ready_preflight": (
        "next: doc_template(doc_type=implement_dense_spec) to author the spec skeleton; "
        "fill required sections; run doc_validate(text=…) or doc_validate(path=…) until "
        "status=pass; record attestation_tokens on the todo's implement-ready assertion; "
        "then todo_distill_implement_gate at Gate-2 close before implement dispatch. "
        "Load: skill `implement-todo`"
    ),
    "edges": (
        "tip: to see edges in entity context, use entity_get with include_edges=true. "
        "To traverse multi-hop, use edge_traverse."
    ),
    "relationships": (
        "tip: to create typed structural links, use relationship_create with "
        "session_id + agent for provenance. Available types: supplement_to, "
        "filed_against, respondent_in, evidence_for, recipient_of, issued_by, "
        "depends_on, blocked_by, owns, references, parent_of/child_of, "
        "sibling_of, related_to, and more."
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
    "graph_reach": (
        "structural blast-radius via relationships/session_edges — for semantic "
        "contradiction search use claim_alignment (or analyze_impact alias)"
    ),
    "impact": (
        "structural blast-radius via relationships/session_edges — for semantic "
        "contradiction search use claim_alignment (or analyze_impact alias)"
    ),
    "claim_alignment": (
        "semantic claim↔entity alignment / contradiction search — for structural "
        "graph reach use graph_reach (or impact alias)"
    ),
    "analyze_impact": (
        "semantic claim↔entity alignment / contradiction search — for structural "
        "graph reach use graph_reach (or impact alias)"
    ),
    "assertion_state": (
        "drill down: assertions(entity_id=…, confidence=confirmed) "
        "or entity_get(intent=card)"
    ),
}

_FRICTION_TICKET_NEXT = (
    "Actionable friction = codified bug ticket (NOT friction() alone), routed as the investigate→execute fix cycle: "
    "investigate+decide (cursor: role=cursor-consult; web: role=web-consult) → dense spec + attribute distillation "
    "at investigate close (files_expected, acceptance_criteria, implement-ready assertion + spec_sha256); "
    "execute default = team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug}) "
    "(server-materialized once attrs distilled); cursor-implement / web-inline = named fallback. "
    "DEFAULT to investigate unless operator says mechanical-only or a dense implement spec exists — "
    "do NOT make cursor-implement the first hop on a bug with open root cause/design. "
    "Lifecycle: investigate → fix → report; pass zoom-out duty (touch-point inventory, "
    "bug-class grep, labeled secondary findings in closeout). "
    "Read: skill `consult-routing` § Codified bug reports or skill `friction-review`."
)

_FRICTION_HINT = (
    "If this failure was unexpected, log friction: "
    'cortex(tool="friction", arguments=\'{"service": "...", '
    '"category": "tool_error", "note": "...", "agent": "..."}\'). '
    "Review open tickets: frictions (cross-service) or assertions on service:{name} "
    'with filter="tool_error"; bus queue: agent_bus list_threads tags=[type:bug]. '
    + _FRICTION_TICKET_NEXT
)

_WORKFLOW_HINTS["friction"] = (
    "Logged observation only — to open a fix cycle use " + _FRICTION_TICKET_NEXT
)

_WORKFLOW_HINTS["frictions"] = (
    "tip: defaults to open assertions on service:* entities (limit=7, intent=summary). "
    "Deepen one row via assertion_get; full rows via intent=full. "
    "Narrow with service, category (tool_error, schema_gap, …), or seeded_by. "
    "Per-service lookup: assertions(entity_id='service:mcp-server', filter='tool_error'). "
    "Close via friction_close after fix. " + _FRICTION_TICKET_NEXT
)

_CORTEX_FORMAT_HINT = (
    "arguments must be a JSON-encoded object string (the MCP tool schema "
    "declares type=string). "
    'Example: cortex(tool="entity_get", arguments=\'{"entity_id": "service:mcp-server"}\')'
)

# Ops whose payloads are routinely large and quote-heavy (transcripts, handoffs).
# A *string* parse failure on these is almost always a hand-escaping problem on
# embedded quotes / newlines / JSON / code fences (frictions 12886, 17227 —
# session_close). Mirror of services/mcp-server _DISPATCH_ARGS_OFFLOAD_HINT so the
# cortex relay path gives the same guidance as the mcp-server dispatch sites.
# See decision:dispatch-arguments-string-wire-form.
_CORTEX_LARGE_PAYLOAD_OPS: frozenset[str] = frozenset(
    {
        "session_close",
        "session_close_preflight",
        "session_handoff_upsert",
    }
)

_CORTEX_OFFLOAD_HINT = (
    " If the payload contains quotes, newlines, or embedded JSON/code fences "
    "(e.g. a large transcript_md, session_summary_md, or handoff_prompt), do not "
    "hand-build the JSON string: write the payload to a file and pass a "
    "file-path parameter instead (session_close: session_summary_md_path / "
    "transcript_jsonl_path / handoff_source_path / source_ref), "
    "or use the /agent-bus CLI, which bypasses MCP shape validation."
)

_CORTEX_HALLUCINATED_TOOLS: dict[str, str] = {
    "search_assertions": "search",
    "search_entities": "entities",
    "get_entity": "entity_get",
    "entity_search": "search",
    "assert_entity": "assert",
    "create_entity": "entity_create",
    "entity_upsert": "entities_bulk_upsert",
    "bulk_create_entities": "entities_bulk_upsert",
    "bulk_upsert_entities": "entities_bulk_upsert",
    "update_entity": "entity_update",
    "create_relationship": "relationship_create",
    "relationship_upsert": "relationships_bulk_upsert",
    "bulk_create_relationships": "relationships_bulk_upsert",
    "bulk_upsert_relationships": "relationships_bulk_upsert",
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
    "decision_status": "assertion_state",
    "get_impact": "graph_reach",
    "impact_analysis": "graph_reach",
    "relationship_impact": "graph_reach",
    "check_impact": "claim_alignment",
    "semantic_impact": "claim_alignment",
    "assertion_align": "claim_alignment",
    "resolve_chunk": "resolve_assertion_chunk",
    "chunk_resolve": "resolve_assertion_chunk",
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
            record(
                "mcp.cortex.dispatch.arguments.invalid",
                tool=tool,
                error=str(exc),
            )
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
