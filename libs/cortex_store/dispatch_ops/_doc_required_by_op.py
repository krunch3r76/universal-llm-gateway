"""Validation-required MCP doc overrides for cortex op descriptors.

Handlers keep ``= None`` defaults so ``handler(**parsed)`` yields structured
``{field} is required`` errors instead of TypeError when callers omit keys.
Signature inspection alone therefore marks those params optional in the MCP
prose descriptor — override here when validation requires them.

Inventory SOT: ``tmp/reviews/mcp-doc-required-field-inventory.md``
(friction 23129 / 23147; todo:mcp-doc-required-field-honesty).
"""

from __future__ import annotations

from collections.abc import Mapping

from ._session_close_doc_type import _SESSION_CLOSE_REQUIRED_FIELDS

_DOC_REQUIRED_BY_OP: Mapping[str, frozenset[str]] = {
    # Friction 23129 — session close attestation fields
    "session_close": frozenset(_SESSION_CLOSE_REQUIRED_FIELDS),
    "session_close_preflight": frozenset(_SESSION_CLOSE_REQUIRED_FIELDS),
    # Batch C — high-traffic write ops
    "assert": frozenset({"entity_id", "claim", "confidence", "evidence"}),
    "supersede": frozenset(
        {"old_assertion_id", "entity_id", "claim", "confidence", "evidence"}
    ),
    "edge_create": frozenset(
        {"session_id", "agent", "from_node", "to_node", "edge_type"}
    ),
    "relationship_create": frozenset({"source_id", "target_id", "type_id"}),
    "friction_close": frozenset({"assertion_id", "resolution_kind"}),
    # Batch D — clear required-error SOT from handler bodies
    "activate": frozenset({"entity_ids"}),
    "analyze_impact": frozenset({"entity_id", "claim"}),
    "claim_alignment": frozenset({"entity_id", "claim"}),
    "assemble_transcript": frozenset({"jsonl_path", "session_id"}),
    "assertion_get": frozenset({"assertion_id"}),
    "assertion_state": frozenset({"entity_id"}),
    "assertion_update": frozenset({"assertion_id"}),
    "case_audit": frozenset({"subject"}),
    "deadline_resolve": frozenset(
        {"deadline_id", "resolution_note", "resolved_at"}
    ),
    "digest": frozenset(
        {"journal_entity_id", "entry_anchor", "entry_text"}
    ),
    "edge_retire": frozenset({"edge_id"}),
    "edge_traverse": frozenset({"node"}),
    "edge_update": frozenset({"edge_id"}),
    "entity_create": frozenset({"id", "type", "name"}),
    "entity_get": frozenset({"entity_id"}),
    "entity_merge": frozenset({"source_id", "target_id"}),
    "entity_rekey": frozenset({"old_id", "new_id"}),
    "entity_retype": frozenset({"entity_id", "new_type"}),
    "entity_update": frozenset({"entity_id"}),
    "fill_gaps": frozenset({"findings"}),
    "friction": frozenset({"owner", "note"}),
    "impact": frozenset({"entity_id"}),
    "graph_reach": frozenset({"entity_id"}),
    "observe": frozenset({"claim"}),
    "relationship_delete": frozenset({"relationship_id"}),
    "relationship_update": frozenset({"relationship_id"}),
    "resolve": frozenset({"uri"}),
    "resolve_assertion_chunk": frozenset({"assertion_id"}),
    "rj_consolidate": frozenset(
        {"agent", "register", "entry", "throughline", "before", "now"}
    ),
    "rj_link": frozenset({"entry_id"}),
    "rj_read": frozenset({"entry_id"}),
    "rj_write": frozenset({"agent", "register", "entry"}),
    "seat_claim": frozenset({"claim_key", "seat"}),
    "seat_heartbeat": frozenset({"holder_id"}),
    "seat_release": frozenset({"holder_id"}),
    "search": frozenset({"query"}),
    "session_audit": frozenset({"session_id"}),
    "tag_assign": frozenset({"tag_name", "entity_id", "assertion_id", "agent"}),
    "tag_list": frozenset({"entity_id"}),
    "tag_resolve": frozenset({"tag_name", "entity_id"}),
}
