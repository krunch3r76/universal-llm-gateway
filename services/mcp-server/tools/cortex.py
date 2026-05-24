"""MCP cortex tool — thin relay to cortex-api POST /dispatch.

Op registry, handlers, workflow hints, friction suggestions, and entity
completeness enrichment live in cortex-api (libs/cortex_store/dispatch_ops/).
This module is the agent-facing MCP tool surface only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool(title="Cortex Knowledge Graph")
    def cortex(tool: str, arguments: str = "{}") -> Any:
        """Cortex knowledge system — entities, assertions, relationships, edges, journals.

                tool: operation name (see table below)
                arguments: JSON-encoded object string (e.g. '{"entity_id": "type:slug"}')

                Operations:
                  entities          (type?, workflow_state?, limit?, query?, for_agent?)         — list entities (workflow_state filters the typed column; use todo_candidates for routine TODO retrieval). `query` is a case-insensitive literal substring filter on entity `id` and `name` (LIKE wildcards `%` and `_` are escaped); composes with `type`, `workflow_state`, and `for_agent`. `for_agent` filters to entities whose `applicable_agents` JSON-list attribute contains either `*` (universal) or the given agent slug; entities without the attribute are treated as universal.
                  entity_get        (entity_id, intent?, include_edges?, edge_limit?, include_compaction_pointers?, debug?, top_k?) — get entity at requested read intent. intent="full" (default) returns the legacy EntityDetail (assertions + relationships + optional reasoning edges). intent="card" returns Card v0 (v2.4 §6.3): identity, status_summary, summary_row, top-K active assertions (default K=7), edge_type_summary, archives_to_count, section_manifest, freshness, reserved predicate_summary slot — projection-aware fetch (~5–10KB instead of full payload). intent in {"cluster","impact"} is reserved for later phases (501). Pass debug=true with intent="card" to attach fetch_plan_row_volume per §7.8.
                  entity_create     (id, type, name, description?, status?, workflow_state?, notes?, aliases?, attributes?, source_uri?) — create entity. workflow_state is the typed per-type workflow column (e.g. todo: open|in_progress|blocked|done|deferred|cancelled); auto-filled to type's initial_state when omitted and the type has a registered schema. Auditor-validatability: entities at status='confirmed' MUST be backed by ≥1 confidence='confirmed' assertion citing the source; session_close audit-gate flags confirmed entities with zero confirmed assertions (confirmed_entity_no_assertions) or with typed attributes unreferenced in any confirmed assertion (confirmed_attribute_no_assertion). See agent_skill:auditor-validatable-confidence.
                  entities_bulk_upsert (entities, if_exists?) — atomically create/update/skip many entities. if_exists ∈ {fail, update, skip}; default fail preserves entity_create's conflict contract. Per-item if_exists overrides the bulk default.
                  entity_update     (entity_id, name?, description?, status?, workflow_state?, notes?, aliases?, attributes?, source_uri?)  — update entity. source_uri auto-recomputes content_hash when set. Auditor-validatability: same audit-gate findings as entity_create apply when promoting status to 'confirmed' — back the confirmed status (and any typed attributes) with confidence='confirmed' assertions. See agent_skill:auditor-validatable-confidence.
                  assertions        (entity_id?, confidence?, review_status?, superseded?, limit?) — list assertions
                  assert            (entity_id, claim, confidence, evidence, derivation_type, confidence_score?, evidence_uris?, seeded_by?, observed_at?, valid_from?, reasoning_summary?, force?, supersedes_id?, acknowledge_audit_gaps?) — write assertion. observed_at auto-fills to now() if absent. valid_from REQUIRED when claim contains a date pattern (YYYY-MM-DD, ISO ts, named dates) unless derivation_type is an observation type. derivation_type values: inference (agent synthesis from prior context), user_statement (user told you directly), agent_observation (tool output / runtime), direct_observation (deterministic read), compression (requires chunk_id + evidence_uris — ingested document), quotation (requires chunk_id + evidence_uris — verbatim quote), commitment, stated, other. Full taxonomy + co-requirements returned inline in 422 body as valid_derivation_types. When confidence='confirmed', advisory validation_warnings fire for auditor-validatability gaps (no evidence_uris, derivation_type:inference, no embedded verbatim quote ≥15 chars for verbatim-expected derivation types); pass acknowledge_audit_gaps=['no_evidence_uris'|'inference_confirmed'|'no_verbatim'] to suppress individual checks with documented intent. See agent_skill:auditor-validatable-confidence.
                  assertion_update  (assertion_id, superseded_by?, valid_until?, confidence?, review_status?, reviewer?, reviewed_at?, review_notes?, predicate_form?, force?) — update assertion metadata (predicate_form=NULL clears the field; other nullable fields treat absent and null identically — use supersede to retract a value). Idempotency guard: when superseded_by is being set and the target row's superseded_by is already non-null, returns 409 Conflict — pass force=true to widen the SQL CAS and overwrite a known-existing supersedence chain. See decision:cortex-api-write-serialization / assertion 9956 (WRITE_LOCK vs SQL CAS doctrine) and friction 9825.
                  supersede         (old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent, evidence_uris?, derivation_type?, valid_from?, reasoning_summary?, seeded_by?, chunk_id?, confidence_score?, acknowledge_audit_gaps?, force?) — atomic close+create. Unspecified optional fields are inherited from the superseded assertion (clone-then-override semantics); pass explicit null to intentionally drop a field. Idempotency guard: when the target old_assertion_id's superseded_by is already non-null, returns 409 Conflict and the new INSERT is rolled back — pass force=true to widen the SQL CAS and overwrite a known-existing supersedence chain. See decision:cortex-api-write-serialization / assertion 9956 (WRITE_LOCK vs SQL CAS doctrine) and friction 9824. When confidence='confirmed', the same advisory auditor-validatability checks as `assert` run against the post-carryover field set; pass acknowledge_audit_gaps=['no_evidence_uris'|'inference_confirmed'|'no_verbatim'] to suppress. See agent_skill:auditor-validatable-confidence.
                  relationships     (entity_id?, type_id?, limit?)          — list active relationships with names, strength
                  relationship_create (source_id, target_id, type_id, role?, strength?, evidence?, session_id?, agent?, resolve_aliases?) — create relationship with optional provenance; resolve_aliases defaults true and reports resolved_aliases when an alias is used.
                  relationships_bulk_upsert (relationships, if_exists?, resolve_aliases?) — atomically create/update/skip many relationships. if_exists ∈ {fail, update, skip}; default fail.
                  relationship_delete (relationship_id)                    — soft-delete an erroneous relationship (row preserved for provenance; no longer appears in list)
                  relationship_update (relationship_id, role?, strength?, evidence?, valid_from?, valid_until?, source_uri?, session_id?, agent?) — patch mutable fields; to fix direction or type, delete and recreate
                  stats             ()                                       — dashboard counts
                  surface_forms     (entity_id?, mention?, mention_type?, limit?) — resolution cache
                  journal_read      (limit?)                                 — recent session journals
                  journal_write     (timestamp, agent, summary, domains?, decisions?, open_items?, entity_ids?, session_id?, prior_session_id?, markdown_content?) — [DEPRECATED: use session_close] write journal; auto-creates transcript entity + continues edge
                  session_close     (session_id, agent, {transcript_jsonl_path | transcript_md}, session_summary_md, summary, domains?, decisions?, open_items?, entity_ids?, prior_session_id?, handoff_prompt?, assistant_label?, dry_run?) — ATOMIC server-side close. Either transcript_jsonl_path (Cursor agents; must resolve under CURSOR_AGENT_TRANSCRIPTS_ROOT) or transcript_md (web agents; verbatim markdown supplied inline) is required. If both are supplied, transcript_jsonl_path wins. The server assembles or accepts the verbatim layer, appends the agent-composed session_summary_md structural layer, writes the file, and atomically creates the transcript entity + journal row + continues edge + optional handoff entry. Rejects missing transcript sources, empty/hollow transcripts, and missing/empty session_summary_md (422 with reason ∈ {transcript_source.missing, transcript_jsonl.invalid, session_summary.invalid, transcript.hollow, transcript.missing_structure, summary.too_short, session_id.invalid, session.already_closed}). Pass dry_run=true to assemble + audit in memory with no file/DB write — returns {"dry_run": true, "would_succeed": true|false, "turn_count": int, "byte_count": int, ...}. Real-call response: {transcript_entity_id, transcript_path, journal_row_id, session_id, content_hash, turn_count, byte_count, handoff_entry_id?}. Quote content_hash in the completion line as response-payload provenance evidence (per provenance-discipline.mdc) — no read-back required. Audit-gate findings surface in _warning (advisory; never blocking in WARN mode). See agent_skill:auditor-validatable-confidence.
                  session_close_preflight (session_id, agent, transcript_jsonl_path, session_summary_md, summary, entity_ids?, defer_gaps?) — Validate args + path sandbox + assemble (in memory) + audit-gate health without writing. Returns {"ok": true, "turn_count": int, "byte_count": int, "audit": {...}, "warnings": [...]} on a path that would close cleanly, or {"ok": false, "error": ..., "reason": ...} otherwise. Use to surface infra/path issues before committing to a full close.
                  assemble_transcript (jsonl_path, session_id, agent?, assistant_label?) — Debug/probe surface only. Returns just the verbatim layer ({"transcript_md": ..., "turn_count": int, "byte_count": int}) from a Cursor agent-transcripts JSONL. NOT used in the close path — session_close consumes the path directly and assembles server-side. Useful when inspecting what cortex-api will compose without committing to a close.
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
                  resolve_assertion_chunk (assertion_id) — resolve assertion's chunk_id to RAG chunk text (URI normalization + verify-on-fetch)
                  friction          (service, category, note, suggestion?, agent?) — log tool/schema/boot friction
                  observe           (claim, entity_id?, agent?) — lightweight observation
                  rj_write          (agent, register, entry, kind?, session_id?, revises?, links?, consolidation_data?) — write reflective journal entry
                  rj_read           (entry_id)                                  — get a reflective journal entry with links
                  rj_list           (agent?, kind?, limit?, offset?)            — list reflective journal entries
                  rj_link           (entry_id, to_entry?, to_entity?, link_type?) — link entry to another entry or entity
                  rj_consolidate    (agent, register, entry, throughline, before, now, tension_points?, contradiction_set?, falsifier?, rendered_shift?, confidence?, source_entry_ids?) — write consolidation entry
                  deadlines         ()                                          — active deadlines
                  deadline_resolve  (deadline_id, resolution_note, resolved_at, evidence?, fulfilling_assertion_id?) — atomic two-write close: confirmed RESOLVED assertion + outcome:met on attributes. Eliminates ghost-deadline boot failures where agents forget the second write.
                  todo_candidates   (q/query?, limit?, workflow_state?, priority?, domain?, domain_exclude?, context?) — ranked TODO retrieval for user intent; prefer over broad open TODO enumeration
                  todo_audit        (stale_days?, limit?, domain?, priority?) — old/open TODO audit for deferral, closure, merge, or spec conversion
                  render_subgraph   (root, hops?, top_k_assertions?, include_superseded?, edge_types?) — render a cortex subgraph as deterministic markdown for session-open materialization.

        Use when:
        - Opening a session and needing a materialized canvas of a decision/todo and its related work (replaces fan-out: entity_get root + edge_traverse + entity_get per neighbor + assertions per node).
        - Building an artifact canvas for chat display.
        - Verifying a roadmap subgraph is intact after writes.

        Do NOT use for:
        - Looking up a single entity → use entity_get with intent='card'.
        - Hybrid search across cortex → use search.
        - Reasoning-edge traversal → use edge_traverse with edge_type filter (session_edges, not structural relationships).

        Hard cap: 50 entities per render. Subgraph_too_large 422 if exceeded — reduce hops or apply edge_types filter.
                  audit             (subject?, kinds?, include_filesystem?) — run gap detectors for integrity audit (graph-only default; include_filesystem=true for fs checks). Returns findings + counts. Phase 1b of cortex-graph-projection-and-audit-primitives.

                confidence values: confirmed / believed / suspected / hypothesized
                review_status values: committed / flagged / staged / rejected

                Workflow chains (successful responses carry a ``_next`` hint field):
                  entity_create → assert → relationship_create → entity_get
                  supersede → entity_get (verify old superseded, new visible)
                  session_close → capture transcript_entity_id AND content_hash from response (and handoff_entry_id when present) → quote content_hash in the user-facing completion line as provenance evidence (no read-back required) → assert on relevant entities using transcript_entity_id as evidence_uri → post bus debrief
                  journal_write [DEPRECATED] → use session_close instead

                Example:
                  cortex(tool="todo_candidates", arguments='{"query": "cortex retrieval", "limit": 5}')
                  cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
        """
        return cx("POST", "/dispatch", {"tool": tool, "arguments": arguments})
