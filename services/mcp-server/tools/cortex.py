"""MCP cortex tool — thin relay to cortex-api POST /dispatch.

Op registry, handlers, workflow hints, friction suggestions, and entity
completeness enrichment live in cortex-api (libs/cortex_store/dispatch_ops/).
This module is the agent-facing MCP tool surface only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._agent_tools import (
    JsonArgStr,
    dispatch_arguments_error,
    parse_dispatch_arguments,
)
from ._cortex_relay import cx

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_cortex_tools(mcp: FastMCP) -> None:
    """Register the dispatch-style cortex tool on the MCP server instance."""

    @mcp.tool(title="Cortex Knowledge Graph")
    def cortex(tool: str, arguments: JsonArgStr = "{}") -> Any:
        """Cortex knowledge system — entities, assertions, relationships, edges, journals.

                tool: operation name (see table below)
                arguments: JSON-encoded object string (e.g. '{"entity_id": "type:slug"}').
                  Large/quote-heavy payloads (session_close transcript_md / handoff_prompt):
                  do NOT hand-build the JSON string — write the payload to a file and pass a
                  file-path param (transcript_jsonl_path / handoff_source_path / source_ref),
                  or use the /agent-bus CLI, which bypasses MCP shape validation.

                Operations:
        # >>> AUTOGEN:cortex-ops (do not edit) >>>
                  activate           (entity_ids?, depth?, max_results?, exclude_ids?, suppress_hubs?, decay_factor?)
                  analyze_impact     (entity_id?, claim?, confidence?, resolve_aliases?, raw_id?) (aliases: claim_alignment)
                  assemble_transcript (jsonl_path?, session_id?, agent?, assistant_label?) — Assemble a verbatim transcript layer from a Cursor JSONL. Args: jsonl_path: path under ``CURSOR_AGENT_TRANSCRIPTS_ROOT`` (absolute or relative to the root).  Sandbox-enforced — paths outside the root return ``{"error", "reason": "path_outside_root"}``. session_id: ``{agent}-YYYY-MM-DD-HHMMSS-{3hex}`` — appears in the H1 line. agent: cosmetic; echoed in the response. assistant_label: heading label for assistant blocks; default ``"Assistant"``. Returns: ``{"transcript_md", "turn_count", "byte_count", "agent"}`` on success, ``{"error", "reason"}`` otherwise.  ``transcript_md`` here is the verbatim layer ONLY — the dispatch caller (debug / probe) is expected to inspect it, NOT to pass it back as a `session_close` argument (that path is dead — see Phase 2 of session-close-server-side-transcript).
                  assert             (entity_id?, claim?, confidence?, evidence?, evidence_uris?, seeded_by?, derivation_type?, confidence_score?, observed_at?, valid_from?, chunk_id?, reasoning_summary?, prospective_summary?, events_json?, artifact_uri?, artifact_storage?, predicate_form?, force?, supersedes_id?, acknowledge_audit_gaps?, attributes?, resolve_aliases?, raw_id?)
                  assertion_get      (assertion_id?) — Read a single assertion by id. Used by `pipelines/predicate_extract/` for the §6.7 idempotency check (predicate_form IS NULL sentinel) without forcing a list-and-filter round trip. Returns the same shape as `_create_assertion_impl`'s `item` field — `predicate_form` included.
                  assertion_state    (entity_id?, resolve_aliases?, raw_id?) — Lightweight ratification/count projection for a single entity.
                  assertion_update   (assertion_id?, superseded_by?, valid_until?, confidence?, confidence_score?, review_status?, reviewer?, reviewed_at?, review_notes?, predicate_form?, force?)
                  assertions         (entity_id?, entity_id_prefix?, filter?, seeded_by?, confidence?, review_status?, superseded?, limit?, intent?, resolve_aliases?, raw_id?)
                  audit              (subject?, kinds?, include_filesystem?) — Run audit detectors for a subject (entity, case, or all). - kinds=None → graph-only set by default (W1 for session_audit). - include_filesystem=true → adds the 4 fs-touching detectors. - Returns {findings: [...], gap_count, criticals, warnings, infos, duration_ms, kinds_run}. - emit=True (full ``cortex(tool='audit')`` op) emits cortex.audit.completed, one cortex.audit.gap.detected per finding, and cortex.audit.budget.exceeded. - emit=False (counts-only callers, e.g. /boot-audit-counters) suppresses all three: the graph today holds ~17k gaps, so per-gap emission on every boot write-amplifies the Event Service and breaks the INSPECT no-side-effects contract. The counts-only caller emits at most one summary event itself.
                  case_audit         (subject?, include_filesystem?) — Full audit for a case entity — graph-only + fs-touching detectors by default. Manual invocation path: includes filesystem detectors (include_filesystem=True default) since the caller expects a wait and needs the complete gap profile. Set include_filesystem=False to scope to graph-only if speed matters.
                  deadline_resolve   (deadline_id?, resolution_note?, resolved_at?, evidence?, fulfilling_assertion_id?, outcome?) — Atomically close a deadline entity: write confirmed assertion + set outcome. ∀ deadline entity: two writes are required to stop it surfacing in deadlines() — a confirmed RESOLVED assertion on the deadline entity AND outcome in its attributes JSON. Agents historically forget the second write; this op performs both reliably.
                  deadlines          ()
                  doc_template       (doc_type?) — Return a dense-spec skeleton that round-trips validate_dense_spec when filled.
                  doc_validate       (doc_type?, text?, path?, source_ref?) — Aggregate implement-ready gate report over resolved dense-spec bytes.
                  edge_create        (session_id?, agent?, from_node?, to_node?, edge_type?, strength?, edge_source?, context?, prompt?, seeded_by?, metadata?)
                  edge_retire        (edge_id?, valid_until?)
                  edge_traverse      (node?, hops?, edge_type?, min_strength?)
                  edge_types         ()
                  edge_update        (edge_id?, strength?, context?, prompt?, metadata?)
                  edges              (from_node?, to_node?, edge_type?, agent?, session_id?, include_retired?, limit?)
                  entities           (type?, workflow_state?, limit?, query?, for_agent?, fields?, include_non_active?)
                  entities_bulk_upsert (entities?, if_exists?)
                  entities_by_content_hash (type?, limit?) — Dedicated content-hash lookup op. Requires content_hash; defaults limit=5.
                  entity_create      (id?, type?, name?, description?, workflow_state?, notes?, aliases?, attributes?, source_uri?)
                  entity_get         (entity_id?, entity_ids?, include_edges?, edge_limit?, intent?, include_superseded?, debug?, top_k?, resolve_aliases?, raw_id?, section?, full_body?) — Dispatch surface for entity_get (v2.4 §6.1). intent="full" — EntityDetail with active assertions + superseded breadcrumb. intent="full-historical" — all rows with full enrichment (audit path). intent="card" — Card v0 via projection-aware fetch (§6.3). intent="card-md" — comprehension-first markdown render (root-only). intent="body" — source_uri markdown (not the KG card). Params: ``section`` (md_read one heading), ``full_body`` (``false``=section manifest only). Default (no section, ``full_body`` unset): whole body. Response includes ``render_mode`` (``"full"`` | ``"manifest"``). intent in {"cluster","impact"} — reserved; rejected until later phases. ``entity_ids`` — batch read; same ``intent``/options for every id; returns ``{"items": [...], "count": N}`` (batch mode supports ``body`` and ``card`` only).
                  entity_merge       (source_id?, target_id?)
                  entity_rekey       (old_id?, new_id?)
                  entity_update      (entity_id?, resolve_aliases?, raw_id?, intent?, adoption?, aliases?, attributes?, confidence_band?, description?, lifecycle?, name?, notes?, source_uri?, workflow_state?)
                  fill_gaps          (findings?, subject?, include_filesystem?) — Return suggested fills for audit findings. Accepts a findings list (from audit/case_audit/session_audit) or a subject to re-run case_audit and generate advice. Advisory only — does not modify state. include_filesystem defaults to False — fast advisory path. Pass True to include filesystem detectors before generating suggestions.
                  friction           (owner?, category?, note?, suggestion?, agent?)
                  friction_close     (assertion_id?, resolution_kind?, agent?, session_id?, evidence?, resolution_note?) — Close an open friction by superseding it with a confirmed resolution row.
                  frictions          (owner?, owner_type?, category?, seeded_by?, superseded?, limit?, intent?) — List open friction assertions across friction-owning entities (service:/agent_skill:/ai_agent:), bracketed [category] claims.
                  impact             (entity_id?, depth?) (aliases: graph_reach)
                  implement_ready_preflight (source_ref?) — Non-writing preflight for todo-sourced implement dispatch.
                  journal_read       (limit?, agent?)
                  journal_write      (timestamp?, agent?, summary?, domains?, decisions?, open_items?, entity_ids?, file_path?, session_id?, prior_session_id?, markdown_content?)
                  observe            (entity_id?, claim?, confidence?, agent?, evidence?)
                  pinned_deliverable_write (rel_path, content, write_if_absent?, dispatch_id?, thread_id?)
                  prose_fact_scan    (principal?, paths?, tier?, dry_run?, unsafe_full_scan?)
                  recon_sidecar_write (label, theme, body, scopes?, queries?, sink_backend?)
                  register_skill_substrate (skill_id, skill_path, case_id?, description?, trigger_phrases?, skill_binding?, session_id?, agent?) — Atomic DB-only composite: agent_skill: + document: + relationship_create. All three writes execute inside a single explicit SQLite transaction so partial failures cannot leave orphaned entities (C1 atomicity). Idempotency (W3): - If agent_skill:<skill_id> exists and matches (name, source_uri canonical, description, trigger_phrases as set), return existing with _status="existing". - If diverges, return composite_conflict with diff + suggested entity_update. - Else create all three rows atomically. Canonical source_uri: workspaces://universal-llm-gateway/.cursor/skills/{skill_id}/SKILL.md (legacy cortex://agent-skills/ rejected). Emits cortex.composite.registered with entity_ids, composite, status.
                  relationship_create (source_id?, target_id?, type_id?, role?, strength?, evidence?, chunk_id?, valid_from?, valid_until?, source_uri?, session_id?, agent?, resolve_aliases?, from_entity?, to_entity?, type?)
                  relationship_delete (relationship_id?)
                  relationship_update (relationship_id?, role?, strength?, evidence?, valid_from?, valid_until?, source_uri?, session_id?, agent?)
                  relationships      (entity_id?, type_id?, limit?, resolve_aliases?, raw_id?)
                  relationships_bulk_upsert (relationships?, if_exists?, resolve_aliases?)
                  render_subgraph    (root?, hops?, top_k_assertions?, include_superseded?, edge_types?, neighbor_fidelity?, hub_rel_threshold?) — Render a subgraph via the shared renderer.
                  resolve            (uri?, tag?)
                  resolve_assertion_chunk (assertion_id?) — Resolve an assertion's chunk_id to RAG chunk text.
                  review_queue       (limit?)
                  rj_consolidate     (agent?, register?, entry?, session_id?, throughline?, before?, now?, tension_points?, contradiction_set?, falsifier?, rendered_shift?, confidence?, source_entry_ids?)
                  rj_link            (entry_id?, to_entry?, to_entity?, link_type?)
                  rj_list            (agent?, kind?, limit?, offset?)
                  rj_read            (entry_id?)
                  rj_write           (agent?, register?, entry?, kind?, session_id?, revises?, links?, consolidation_data?)
                  search             (query?, limit?, superseded?, entity_type?, intent?)
                  session_audit      (session_id?, entity_ids?, defer_gaps?) — Manually invoke the session audit for a session ID. Runs graph-only detectors scoped to entity_ids (or full graph if empty). Does not block or modify session state — advisory only. Use case_audit for a full (graph + fs) audit of a case entity.
                  session_close      (session_id?, agent?, transcript_jsonl_path?, transcript_md?, session_summary_md?, summary?, transcript_depth?, domains?, decisions?, open_items?, entity_ids?, prior_session_id?, handoff_prompt?, handoff_source_path?, handoff_source_section?, expected_handoff_prompt?, expected_derived_handoff_prompt_sha256?, expected_source_file_sha256?, assistant_label?, source_ref?, source_ref_derivation?, defer_gaps?, promote_todos?, validate_attestation?, dry_run?) — Atomic session close (server-side transcript derivation). Flow: 1. Cheap arg + session_id + summary validation. 2. Audit gate — may BLOCK before any file/DB write. 3. If ``dry_run``: assemble in-memory, validate, return preview. 4. Hand off to the route handler (`_close_session_impl`) which owns the atomic boundary: resolve path → assemble verbatim → compose → write file → DB tx → content_hash. 5. Append audit warnings + post-close detectors + structural warnings to the response. ``transcript_depth`` (default ``"verbatim"``) selects the archival layer — ``light`` writes a structural-only file with the transcript entity flagged as non-enrichment-eligible; ``none`` writes no file and no transcript entity, only the journal row (plus the continues edge). ``handoff_prompt`` / ``handoff_source_path`` at ``none`` return 422 ``handoff.requires_transcript_entity`` — use ``light`` minimum. Continuity is preserved at all depths. See session-close-server-side-transcript Phase 2 for the architecture rewrite; the route handler in `routes/session_journals.py` is the single atomic boundary.
                  session_close_preflight (session_id?, agent?, transcript_jsonl_path?, transcript_md?, session_summary_md?, summary?, transcript_depth?, handoff_prompt?, handoff_source_path?, entity_ids?, defer_gaps?, assistant_label?) — Validate args + path sandbox + audit-gate health WITHOUT writing. Returns ``{"ok": True, "audit": {...}, "turn_count": int}`` on a path that would succeed at close time, or ``{"ok": False, "error", "reason"}`` otherwise.  Verbatim assembly is performed in-memory (no file written, no DB row) so the agent learns about a bad JSONL before paying for the audit and DB tx. ``transcript_depth`` (default ``"verbatim"``) selects the archival depth — ``none`` skips assembly entirely; ``light`` derives the composed file from ``session_summary_md`` alone.
                  session_handoff_upsert (session_id, handoff_prompt, handoff_source_path?, handoff_source_section?, expected_handoff_prompt?, expected_derived_handoff_prompt_sha256?, expected_source_file_sha256?) — Upsert handoff_prompt on a closed session (journal row + transcript mirror).
                  stats              ()
                  supersede          (old_assertion_id?, entity_id?, claim?, confidence?, evidence?, evidence_uris?, valid_from?, derivation_type?, reasoning_summary?, seeded_by?, chunk_id?, confidence_score?, session_id?, agent?, acknowledge_audit_gaps?, force?)
                  surface_forms      (entity_id?, mention?, mention_type?, limit?)
                  tag_assign         (tag_name?, entity_id?, assertion_id?, agent?, resolve_aliases?, raw_id?)
                  tag_list           (entity_id?, resolve_aliases?, raw_id?)
                  tag_resolve        (tag_name?, entity_id?, resolve_aliases?, raw_id?)
                  thread_sidecar_write (thread, subject, content, from_agent?, execution_id?, oversized?)
                  todo_audit         (stale_days?, limit?, domain?, priority?)
                  todo_candidates    (q?, query?, limit?, workflow_state?, priority?, domain?, domain_exclude?, context?)
                  todo_close_sidecar (todo_id?, summary?, evidence?, reasoning_summary?, references?, agent?, session_id?, closed_at?) — Write the standardized closure markdown sidecar + set the entity pointer. Produces ``notes/system/todos/{slug}-closure.md`` under the cortex sandbox and sets ``attributes.closure_summary_uri`` on the todo entity (merge — existing attributes are preserved). Returns the canonical URI so the caller can cite it in the closure assertion's ``evidence_uris``.
                  todo_distill_implement_gate (todo_id?, files_expected?, acceptance_criteria?, required_skills?, claim?, evidence?, agent?, session_id?, seeded_by?, density_triage?, source_uri?) — Wire implement-admission gate fields atomically at Gate-2 close.
                  walk_subgraph      (root?, hops?, edge_types?, direction?, entity_cap?, include_counts?, promote_hubs?, hub_rel_threshold?) — Walk a subgraph — lean topology without assertion canvas.
# <<< AUTOGEN:cortex-ops <<<

                confidence values: confirmed / believed / suspected / hypothesized
                review_status values: committed / flagged / staged / rejected

                Workflow chains (successful responses carry a ``_next`` hint field):
                  entity_create → assert → relationship_create → entity_get
                  supersede → entity_get (verify old superseded, new visible)
                  session_close → capture transcript_entity_id AND content_hash from response → quote content_hash in the user-facing completion line as provenance evidence (no read-back required) → assert on relevant entities using transcript_entity_id as evidence_uri → post bus debrief
                  journal_write [DEPRECATED] → use session_close instead

                Example:
                  cortex(tool="todo_candidates", arguments='{"query": "cortex retrieval", "limit": 5}')
                  cortex(tool="assert", arguments='{"entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "..."}')
                  cortex(tool="search", arguments='{"query": "embedding \\"recall\\" tradeoffs"}')  # embedded quotes JSON-escaped; for big payloads use a file-path param
        """
        if parse_dispatch_arguments(arguments) is None:
            return dispatch_arguments_error(
                arguments, example='{"entity_id": "type:slug"}', tool="cortex"
            )
        return cx("POST", "/dispatch", {"tool": tool, "arguments": arguments})
