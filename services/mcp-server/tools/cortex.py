"""MCP cortex tool — thin relay to cortex-api POST /dispatch.

Op registry, handlers, workflow hints, friction suggestions, and entity
completeness enrichment live in cortex-api (libs/cortex_store/dispatch_ops/).
This module is the agent-facing MCP tool surface only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ._cortex_relay import _cx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool(title="Cortex Knowledge Graph")
    def cortex(tool: str, arguments: dict[str, Any] | str = "{}") -> Any:
        """Cortex knowledge system — entities, assertions, relationships, edges, journals.

        tool: operation name (see table below)
        arguments: operation arguments as an object or a JSON string

        Operations:
          entities          (type?, workflow_state?, limit?)         — list entities (workflow_state filters the typed column; use todo_candidates for routine TODO retrieval)
          entity_get        (entity_id, include_edges?, edge_limit?) — get entity with assertions + relationships + optional reasoning edges
          entity_create     (id, type, name, description?, status?, workflow_state?, notes?, aliases?, attributes?, source_uri?) — create entity. workflow_state is the typed per-type workflow column (e.g. todo: open|in_progress|done|deferred|cancelled); auto-filled to type's initial_state when omitted and the type has a registered schema.
          entity_update     (entity_id, name?, description?, status?, workflow_state?, notes?, aliases?, attributes?, source_uri?)  — update entity. source_uri auto-recomputes content_hash when set.
          assertions        (entity_id?, confidence?, review_status?, superseded?, limit?) — list assertions
          assert            (entity_id, claim, confidence, evidence, derivation_type, confidence_score?, evidence_uris?, seeded_by?, observed_at?, valid_from?, reasoning_summary?, force?, supersedes_id?) — write assertion. observed_at auto-fills to now() if absent. valid_from REQUIRED when claim contains a date pattern (YYYY-MM-DD, ISO ts, named dates) unless derivation_type is an observation type. derivation_type values: inference (agent synthesis from prior context), user_statement (user told you directly), agent_observation (tool output / runtime), direct_observation (deterministic read), compression (requires chunk_id + evidence_uris — ingested document), quotation (requires chunk_id + evidence_uris — verbatim quote), commitment, stated, other. Full taxonomy + co-requirements returned inline in 422 body as valid_derivation_types.
          assertion_update  (assertion_id, superseded_by?, valid_until?, confidence?, review_status?) — update assertion
          supersede         (old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent) — atomic close+create
          relationships     (entity_id?, type_id?, limit?)          — list with names, strength
          relationship_create (source_id, target_id, type_id, role?, strength?, evidence?, session_id?, agent?) — create relationship with optional provenance
          stats             ()                                       — dashboard counts
          surface_forms     (entity_id?, mention?, mention_type?, limit?) — resolution cache
          journal_read      (limit?)                                 — recent session journals
          journal_write     (timestamp, agent, summary, domains?, decisions?, open_items?, entity_ids?, session_id?, prior_session_id?, markdown_content?) — [DEPRECATED: use session_close] write journal; auto-creates transcript entity + continues edge
          session_close     (session_id, agent, transcript_md, summary, domains?, decisions?, open_items?, entity_ids?, prior_session_id?) — ATOMIC session close: validates transcript, writes file, creates entity + journal row + continues edge in one call. Rejects stubs.
          review_queue      (limit?)                                 — provisional entities + flagged assertions
          edge_create       (session_id, agent, from_node, to_node, edge_type, strength?, context?) — seed reasoning connection. Common edge_types: depends_on, leads_to, caused_by, contradicts, supersedes, relates_to, evidence_for, corroborates, derived_from, extends, promises, expects, continues, analogous_to, reasoned_about. Call edge_types for full taxonomy + directionality.
          edges             (from_node?, to_node?, edge_type?, agent?, session_id?, limit?) — query edges
          edge_traverse     (node, hops?, edge_type?, min_strength?) — graph traversal (1-2 hops)
          edge_retire       (edge_id, valid_until?)                  — retire an edge
          edge_types        ()                                        — list registered edge types
          search            (query, limit?, superseded?, entity_type?) — FTS5 fulltext search over assertions
          analyze_impact    (entity_id, claim, confidence?)            — semantic pre-write impact analysis (C1)
          activate          (entity_ids, depth?, max_results?, exclude_ids?, suppress_hubs?, decay_factor?) — spreading activation
          resolve           (uri, tag?)                                — cortex:// URI resolution
          tag_assign        (tag_name, entity_id, assertion_id, agent) — assign/move a named tag
          tag_list          (entity_id)                                — list tags for entity
          tag_resolve       (tag_name, entity_id)                      — resolve tag to assertion
          impact            (entity_id, depth?)                        — transitive impact BFS
          ingest_document   (source_uri, content, observer?, source_date?) — chunk and ingest a document
          assert_from_chunk (chunk_id, entity_id, claim, confidence, evidence, ...) — write assertion linked to a chunk
          friction          (service, category, note, suggestion?, agent?) — log tool/schema/boot friction
          observe           (claim, entity_id?, agent?) — lightweight observation
          rj_write          (agent, register, entry, kind?, session_id?, revises?, links?, consolidation_data?) — write reflective journal entry
          rj_read           (entry_id)                                  — get a reflective journal entry with links
          rj_list           (agent?, kind?, limit?, offset?)            — list reflective journal entries
          rj_link           (entry_id, to_entry?, to_entity?, link_type?) — link entry to another entry or entity
          rj_consolidate    (agent, register, entry, throughline, before, now, tension_points?, contradiction_set?, falsifier?, rendered_shift?, confidence?, source_entry_ids?) — write consolidation entry
          deadlines         ()                                          — active deadlines
          todo_candidates   (q/query?, limit?, workflow_state?, priority?, domain?, domain_exclude?, context?) — ranked TODO retrieval for user intent; prefer over broad open TODO enumeration
          todo_audit        (stale_days?, limit?, domain?, priority?) — old/open TODO audit for deferral, closure, merge, or spec conversion

        confidence values: confirmed / believed / suspected / hypothesized
        review_status values: committed / flagged / staged / rejected

        Workflow chains (successful responses carry a ``_next`` hint field):
          entity_create → assert → relationship_create → entity_get
          ingest_document → assert_from_chunk → relationship_create → entity_get
          supersede → entity_get (verify old superseded, new visible)
          session_close → assert on relevant entities (seed decisions/observations) → post bus debrief → entity_get (confirm transcript entity)
          journal_write [DEPRECATED] → use session_close instead

        Example:
          cortex(tool="todo_candidates", arguments='{"query": "cortex retrieval", "limit": 5}')
          cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
        """
        return _cx("POST", "/dispatch", {"tool": tool, "arguments": arguments})
