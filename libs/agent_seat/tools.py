"""Tool-call schemas for agent-seat tool loops.

OpenAI function-calling definitions shared between the MCP ``team_dispatch``
relay and the pipeline ``frontier_dispatch_v1`` handler. Both surfaces use the
same OpenAI-shape tool schema — providers that speak native Anthropic / xAI /
Google formats are translated by upstream adapters (MCP's ``llm_adapters``;
Stargate's cloud-proxy).

Two tiers:

- ``TOOL_DEFINITIONS`` — static cortex dispatch fallback for read-heavy workloads.
- ``TEAM_TOOL_DEFINITIONS`` — cortex + agent_bus (write access + inter-agent
  messaging). Superset of the read tier.

RAG is sourced from the live MCP ``rag`` descriptor, not a local shim.
The cortex op registry lives in cortex-api; both tiers share the same tool
schema here and the same op space at the /dispatch endpoint.
"""

from __future__ import annotations

from typing import Any


def _fn(
    name: str,
    desc: str,
    props: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Build an OpenAI function-calling tool definition."""
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": desc, "parameters": schema},
    }


# >>> AUTOGEN:cortex-ops (do not edit) >>>
_CORTEX_OPS_DOC = (
    "  activate (entity_ids, depth?, max_results?, exclude_ids?, suppress_hubs?, decay_factor?)\n"
    "  analyze_impact (entity_id, claim, confidence?, resolve_aliases?, raw_id?) (aliases: claim_alignment)\n"
    "  assemble_transcript (jsonl_path, session_id, agent?, assistant_label?) — Assemble a verbatim transcript layer from a Cursor JSONL. Args: jsonl_path: path under ``CURSOR_AGENT_TRANSCRIPTS_ROOT`` (absolute or relative to the root).  Sandbox-enforced — paths outside the root return ``{\"error\", \"reason\": \"path_outside_root\"}``. session_id: ``{agent}-YYYY-MM-DD-HHMMSS-{3hex}`` — appears in the H1 line. agent: cosmetic; echoed in the response. assistant_label: heading label for assistant blocks; default ``\"Assistant\"``. Returns: ``{\"transcript_md\", \"turn_count\", \"byte_count\", \"agent\"}`` on success, ``{\"error\", \"reason\"}`` otherwise.  ``transcript_md`` here is the verbatim layer ONLY — the dispatch caller (debug / probe) is expected to inspect it, NOT to pass it back as a `session_close` argument (that path is dead — see Phase 2 of session-close-server-side-transcript).\n"
    "  assert (entity_id, claim, confidence, evidence, evidence_uris?, seeded_by?, derivation_type?, confidence_score?, observed_at?, valid_from?, chunk_id?, reasoning_summary?, prospective_summary?, events_json?, artifact_uri?, artifact_storage?, predicate_form?, force?, supersedes_id?, acknowledge_audit_gaps?, dry_run?, attributes?, resolve_aliases?, raw_id?)\n"
    "  assertion_get (assertion_id) — Read a single assertion by id. Used by `pipelines/predicate_extract/` for the §6.7 idempotency check (predicate_form IS NULL sentinel) without forcing a list-and-filter round trip. Returns the same shape as `_create_assertion_impl`'s `item` field — `predicate_form` included.\n"
    "  assertion_state (entity_id, resolve_aliases?, raw_id?) — Lightweight ratification/count projection for a single entity.\n"
    "  assertion_update (assertion_id, superseded_by?, valid_from?, valid_until?, confidence?, confidence_score?, review_status?, reviewer?, reviewed_at?, review_notes?, evidence_uris?, reasoning_summary?, predicate_form?, prospective_summary?, events_json?, force?)\n"
    "  assertions (entity_id?, entity_id_prefix?, filter?, seeded_by?, confidence?, review_status?, superseded?, limit?, intent?, resolve_aliases?, raw_id?)\n"
    "  audit (subject?, kinds?, include_filesystem?) — Run audit detectors for a subject (entity, case, or all). - kinds=None → graph-only set by default (W1 for session_audit). - include_filesystem=true → adds the 4 fs-touching detectors. - Returns {findings: [...], gap_count, criticals, warnings, infos, duration_ms, kinds_run}. - emit=True (full ``cortex(tool='audit')`` op) emits cortex.audit.completed, one cortex.audit.gap.detected per finding, and cortex.audit.budget.exceeded. - emit=False (counts-only callers, e.g. /boot-audit-counters) suppresses all three: the graph today holds ~17k gaps, so per-gap emission on every boot write-amplifies the Event Service and breaks the INSPECT no-side-effects contract. The counts-only caller emits at most one summary event itself.\n"
    "  case_audit (subject, include_filesystem?) — Full audit for a case entity — graph-only + fs-touching detectors by default. Manual invocation path: includes filesystem detectors (include_filesystem=True default) since the caller expects a wait and needs the complete gap profile. Set include_filesystem=False to scope to graph-only if speed matters.\n"
    "  deadline_resolve (deadline_id, resolution_note, resolved_at, evidence?, fulfilling_assertion_id?, outcome?) — Atomically close a deadline entity: write confirmed assertion + set outcome. ∀ deadline entity: two writes are required to stop it surfacing in deadlines() — a confirmed RESOLVED assertion on the deadline entity AND outcome in its attributes JSON. Agents historically forget the second write; this op performs both reliably.\n"
    "  deadlines ()\n"
    "  digest (journal_entity_id, entry_anchor, entry_text, journal_uri?, auto_segment?, entry_date?, action?, job_id?, tick_limit?) — Watermark → extract → verify → attach/map → dedup → stage → ledger.\n"
    "  doc_template (doc_type?) — Return a dense-spec skeleton that round-trips validate_dense_spec when filled.\n"
    "  doc_validate (doc_type?, text?, path?, source_ref?) — Aggregate implement-ready gate report over resolved dense-spec bytes.\n"
    "  edge_create (session_id, agent, from_node, to_node, edge_type, strength?, edge_source?, context?, prompt?, seeded_by?, metadata?)\n"
    "  edge_retire (edge_id, valid_until?)\n"
    "  edge_traverse (node, hops?, edge_type?, min_strength?)\n"
    "  edge_types ()\n"
    "  edge_update (edge_id, strength?, context?, prompt?, metadata?)\n"
    "  edges (from_node?, to_node?, edge_type?, agent?, session_id?, include_retired?, limit?)\n"
    "  endeavor_dispose_row (host?, row_id?, disposition?, reason?, authority?)\n"
    "  endeavor_lock_ready (host?, deliverable?)\n"
    "  endeavor_repair_t1 ()\n"
    "  endeavor_write_row (host?, fields?)\n"
    "  entities (type?, category?, workflow_state?, limit?, query?, for_agent?, fields?, include_non_active?)\n"
    "  entities_bulk_upsert (entities?, if_exists?)\n"
    "  entities_by_content_hash (type?, limit?) — Dedicated content-hash lookup op. Requires content_hash; defaults limit=5.\n"
    "  entity_create (id, type, name, description?, workflow_state?, notes?, aliases?, attributes?, source_uri?)\n"
    "  entity_get (entity_id, entity_ids?, include_edges?, edge_limit?, intent?, include_superseded?, debug?, top_k?, resolve_aliases?, raw_id?, section?, full_body?) — Dispatch surface for entity_get (v2.4 §6.1). intent=\"full\" — EntityDetail with active assertions + superseded breadcrumb. intent=\"full-historical\" — all rows with full enrichment (audit path). intent=\"card\" — Card v0 via projection-aware fetch (§6.3). intent=\"card-md\" — comprehension-first markdown render (root-only). intent=\"body\" — source_uri markdown (not the KG card). Params: ``section`` (md_read one heading), ``full_body`` (``false``=section manifest only). Default (no section, ``full_body`` unset): whole body. Response includes ``render_mode`` (``\"full\"`` | ``\"manifest\"``). intent in {\"cluster\",\"impact\"} — reserved; rejected until later phases. ``entity_ids`` — batch read; same ``intent``/options for every id; returns ``{\"items\": [...], \"count\": N}`` (batch mode supports ``body`` and ``card`` only).\n"
    "  entity_merge (source_id, target_id)\n"
    "  entity_rekey (old_id, new_id)\n"
    "  entity_retype (entity_id, new_type, force?)\n"
    "  entity_update (entity_id, resolve_aliases?, raw_id?, intent?, adoption?, aliases?, attributes?, confidence_band?, description?, lifecycle?, name?, notes?, source_uri?, workflow_state?)\n"
    "  fill_gaps (findings, subject?, include_filesystem?) — Return suggested fills for audit findings. Accepts a findings list (from audit/case_audit/session_audit) or a subject to re-run case_audit and generate advice. Advisory only — does not modify state. include_filesystem defaults to False — fast advisory path. Pass True to include filesystem detectors before generating suggestions.\n"
    "  friction (owner, category?, note, claim?, suggestion?, agent?, session_id?, charter_root?, window_index?, root_thread?, cp_ordinal?, scoreboard_uri?, actionable?, actionable_false_reason?, checkpoint_turn?, evidence_uris?, defer_enqueue?, confidence?, confidence_score?) — Log a friction assertion; protocol category requires a complete anchor. Anchor variants: charter ``{charter_root, window_index}`` or continuity ``{root_thread, cp_ordinal}``. ``confidence`` is honoured when supplied; omitted → hypothesized/0.5. Invalid values are rejected — never silently downgraded.\n"
    "  friction_close (assertion_id, resolution_kind, agent?, session_id?, evidence?, resolution_note?) — Close an open friction by superseding it with a confirmed resolution row.\n"
    "  frictions (owner?, owner_type?, category?, seeded_by?, charter_root?, window_index?, anchor_kind?, anchor_root?, anchor_seq?, actionable?, since?, superseded?, limit?, intent?) — List open friction assertions across friction-owning entities (service:/agent_skill:/ai_agent:), bracketed [category] claims.\n"
    "  impact (entity_id, depth?) (aliases: graph_reach)\n"
    "  implement_ready_preflight (source_ref?) — Non-writing preflight for todo-sourced implement dispatch.\n"
    "  journal_read (limit?, agent?)\n"
    "  observe (entity_id?, claim, confidence?, agent?, evidence?)\n"
    "  pinned_deliverable_write (rel_path, content, write_if_absent?, dispatch_id?, thread_id?)\n"
    "  prose_fact_scan (principal?, paths?, tier?, dry_run?, unsafe_full_scan?)\n"
    "  recon_sidecar_write (label, theme, body, scopes?, queries?, sink_backend?)\n"
    "  register_skill_substrate (skill_id, skill_path, case_id?, description?, trigger_phrases?, skill_binding?, session_id?, agent?) — Atomic DB-only composite: agent_skill: + document: + relationship_create. C1 atomicity — CREATE PATH: all three writes (agent_skill entity, document entity, keystone_of relationship, plus the optional uses_skill relationship) execute on a single connection under WRITE_LOCK inside one explicit ``BEGIN IMMEDIATE`` … ``COMMIT``, via the conn-taking impls ``create_entity_impl(conn, …, commit=False)`` and ``create_relationship_on_conn(conn, …, commit=False)``. A mid-composite failure rolls back every member — partial failures cannot leave orphaned entities. The ``_op_*`` wrappers are bypassed on this path, which drops their advisory-only machinery (write-discipline nudge, collision warning, per-member ``mcp.cortex.*.created`` events); the RAG source-changed nudge is covered by the periodic backstop for ``commit=False`` callers. MATCHING-PATH BACKFILL is intentionally NON-atomic per member: each backfilled member (document entity, keystone relationship) is written by the wrapper ops on their own connections and is independently durable and idempotent — a failed backfill converges on retry. Idempotency (W3): - If agent_skill:<skill_id> exists and matches (name, source_uri canonical, description, trigger_phrases as set), return existing with _status=\"existing\"; the document member and keystone relationship are backfilled when missing (composite completion — the substantiation migration registers over pre-existing skill entities, which must end up fully substantiated). - If diverges, return composite_conflict with diff + suggested entity_update (the suggested update covers name/source_uri/description/attributes so a re-register after applying it converges to \"existing\"). - Else create all three rows atomically. Canonical ``source_uri`` for new registrations: ``workspaces://universal-llm-gateway/.cursor/skills/{skill_id}/SKILL.md``. Legacy ``cortex://agent-skills/`` paths are rejected (invalid_skill_path). Emits cortex.composite.registered with entity_ids, composite, status.\n"
    "  relationship_create (source_id, target_id, type_id, role?, strength?, evidence?, chunk_id?, valid_from?, valid_until?, source_uri?, session_id?, agent?, resolve_aliases?, from_entity?, to_entity?, type?)\n"
    "  relationship_delete (relationship_id)\n"
    "  relationship_update (relationship_id, role?, strength?, evidence?, valid_from?, valid_until?, source_uri?, session_id?, agent?)\n"
    "  relationships (entity_id?, type_id?, limit?, resolve_aliases?, raw_id?)\n"
    "  relationships_bulk_upsert (relationships?, if_exists?, resolve_aliases?)\n"
    "  render_subgraph (root?, hops?, top_k_assertions?, include_superseded?, edge_types?, neighbor_fidelity?, hub_rel_threshold?) — Render a subgraph via the shared renderer.\n"
    "  resolve (uri, tag?)\n"
    "  resolve_assertion_chunk (assertion_id) — Resolve an assertion's chunk_id to RAG chunk text.\n"
    "  review_queue (limit?)\n"
    "  rj_consolidate (agent, register, entry, session_id?, throughline, before, now, tension_points?, contradiction_set?, falsifier?, rendered_shift?, confidence?, source_entry_ids?)\n"
    "  rj_link (entry_id, to_entry?, to_entity?, link_type?)\n"
    "  rj_list (agent?, kind?, limit?, offset?)\n"
    "  rj_read (entry_id)\n"
    "  rj_write (agent, register, entry, kind?, session_id?, revises?, links?, consolidation_data?)\n"
    "  search (query, limit?, superseded?, entity_type?, intent?)\n"
    "  seat_claim (claim_key, seat, ttl_s?, metadata?)\n"
    "  seat_claims_list (claim_key?, seat?, include_ended?)\n"
    "  seat_heartbeat (holder_id)\n"
    "  seat_release (holder_id)\n"
    "  session_audit (session_id, entity_ids?, defer_gaps?) — Manually invoke the session audit for a session ID. Runs graph-only detectors scoped to entity_ids (or full graph if empty). Does not block or modify session state — advisory only. Use case_audit for a full (graph + fs) audit of a case entity.\n"
    "  session_close (session_id, agent, transcript_jsonl_path?, transcript_md?, session_summary_md, session_summary_md_path?, summary, transcript_depth?, domains?, decisions?, open_items?, entity_ids?, prior_session_id?, handoff_prompt?, handoff_source_path?, handoff_source_section?, expected_handoff_prompt?, expected_derived_handoff_prompt_sha256?, expected_source_file_sha256?, assistant_label?, source_ref?, source_ref_derivation?, defer_gaps?, promote_todos?, dry_run?, digest?) — Atomic session close (server-side transcript derivation). Flow: 1. Cheap arg + session_id + summary validation. 2. Audit gate — may BLOCK before any file/DB write. 3. If ``dry_run``: assemble in-memory, validate, return preview. 4. Hand off to the route handler (`_close_session_impl`) which owns the atomic boundary: resolve path → assemble verbatim → compose → write file → DB tx → content_hash. 5. Append audit warnings + post-close detectors + structural warnings to the response. ``transcript_depth`` (default ``\"verbatim\"``) selects the archival layer — ``light`` writes a structural-only file with the transcript entity flagged as non-enrichment-eligible; ``none`` writes no file and no transcript entity, only the journal row (plus the continues edge). ``handoff_prompt`` / ``handoff_source_path`` at ``none`` return 422 ``handoff.requires_transcript_entity`` — use ``light`` minimum. Continuity is preserved at all depths. ``session_summary_md_path`` (optional) loads the structural summary from a CORTEX_FILES_ROOT-relative file. When both path and inline ``session_summary_md`` are set, **path wins**. See session-close-server-side-transcript Phase 2 for the architecture rewrite; the route handler in `routes/session_journals.py` is the single atomic boundary.\n"
    "  session_close_preflight (session_id, agent, transcript_jsonl_path?, transcript_md?, session_summary_md, session_summary_md_path?, summary, transcript_depth?, handoff_prompt?, handoff_source_path?, entity_ids?, defer_gaps?, assistant_label?) — Validate args + path sandbox + audit-gate health WITHOUT writing. Returns ``{\"ok\": True, \"audit\": {...}, \"turn_count\": int}`` on a path that would succeed at close time, or ``{\"ok\": False, \"error\", \"reason\"}`` otherwise.  Verbatim assembly is performed in-memory (no file written, no DB row) so the agent learns about a bad JSONL before paying for the audit and DB tx. ``transcript_depth`` (default ``\"verbatim\"``) selects the archival depth — ``none`` skips assembly entirely; ``light`` derives the composed file from ``session_summary_md`` alone. ``session_summary_md_path`` (optional) loads the structural summary from a CORTEX_FILES_ROOT-relative file. When both path and inline ``session_summary_md`` are set, **path wins**.\n"
    "  session_handoff_upsert (session_id, handoff_prompt, handoff_source_path?, handoff_source_section?, expected_handoff_prompt?, expected_derived_handoff_prompt_sha256?, expected_source_file_sha256?) — Upsert handoff_prompt on a closed session (journal row + transcript mirror).\n"
    "  staging_approve (staging_id?, reviewer?)\n"
    "  staging_batch_approve (staging_ids?, ledger_id?, reviewer?)\n"
    "  staging_list (source_uri?, limit?)\n"
    "  staging_reject (staging_id?, reviewer?)\n"
    "  stats ()\n"
    "  supersede (old_assertion_id, entity_id, claim, confidence, evidence, evidence_uris?, valid_from?, derivation_type?, reasoning_summary?, seeded_by?, chunk_id?, confidence_score?, session_id?, agent?, acknowledge_audit_gaps?, force?)\n"
    "  surface_forms (entity_id?, mention?, mention_type?, limit?)\n"
    "  tag_assign (tag_name, entity_id, assertion_id, agent, resolve_aliases?, raw_id?)\n"
    "  tag_list (entity_id, resolve_aliases?, raw_id?)\n"
    "  tag_resolve (tag_name, entity_id, resolve_aliases?, raw_id?)\n"
    "  thread_sidecar_write (thread, subject, content, from_agent?, execution_id?, oversized?, sidecar_slug?)\n"
    "  todo_audit (stale_days?, limit?, domain?, priority?)\n"
    "  todo_candidates (q?, query?, limit?, workflow_state?, priority?, domain?, domain_exclude?, context?)\n"
    "  todo_close_sidecar (todo_id?, summary?, evidence?, reasoning_summary?, references?, agent?, session_id?, closed_at?) — Write the standardized closure markdown sidecar + set the entity pointer. Produces ``notes/system/todos/{slug}-closure.md`` under the cortex sandbox and sets ``attributes.closure_summary_uri`` on the todo entity (merge — existing attributes are preserved). Returns the canonical URI so the caller can cite it in the closure assertion's ``evidence_uris``.\n"
    "  todo_distill_implement_gate (todo_id?, files_expected?, acceptance_criteria?, required_skills?, claim?, evidence?, agent?, session_id?, seeded_by?, density_triage?, source_uri?, recon_waive_reason_code?, recon_waive_reason?) — Wire implement-admission gate fields atomically at Gate-2 close.\n"
    "  view_render (document_id?, mode?, root_id?, view_profile?, narrative_sections?, as_of_system?, as_of_valid?, agent?, session_id?) — Render or refresh a derived view document from graph state and recipe data.\n"
    "  walk_subgraph (root?, hops?, edge_types?, direction?, entity_cap?, include_counts?, promote_hubs?, hub_rel_threshold?) — Walk a subgraph — lean topology without assertion canvas.\n"
)
# <<< AUTOGEN:cortex-ops <<<

CORTEX_TOOL_DEFINITION: dict[str, Any] = _fn(
    "cortex",
    "Cortex knowledge system — unified dispatch tool for the full Cortex "
    "surface.\n\n"
    "Key operations:\n"
    + _CORTEX_OPS_DOC
    + "\n"
    "confidence: confirmed / believed / suspected / hypothesized\n"
    "arguments MUST be a JSON string or an object.",
    {
        "tool": {
            "type": "string",
            "description": (
                "Operation name (e.g. entity_get, assert, observe, "
                "search, session_close, edge_create)"
            ),
        },
        "arguments": {
            "type": "string",
            "description": (
                "JSON string (or object) of operation arguments. "
                'Example: \'{"entity_id": "person:jane-doe"}\''
            ),
        },
    },
    ["tool"],
)


# Safe alias for the Brave Search MCP tool. The MCP tool is named
# "web_search" at the server layer, but that name collides with
# Claude's and Gemini's native search capability when injected into
# frontier model tool lists.  Callers MUST use "brave_search" — the
# executor translates the call to "web_search" on the MCP side.
BRAVE_SEARCH_TOOL_DEFINITION: dict[str, Any] = _fn(
    "brave_search",
    "Live web search via the Brave Search API. Returns current search "
    "results for the given query. Use this for real-time lookups — "
    "prices, news, recent events, URLs. ALWAYS use this tool, never "
    "the model's native web_search capability.",
    {
        "query": {"type": "string", "description": "Search query"},
        "max_results": {
            "type": "integer",
            "description": "Max results to return (default 5, max 10)",
        },
    },
    ["query"],
)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
]


_AGENT_BUS_TOOL_DEFINITION: dict[str, Any] = _fn(
    "agent_bus",
    "Inter-agent message bus — threads, turns, read/reply coordination.\n\n"
    "Body convention: keep post/reply bodies brief. Long handoffs, specs, "
    "reviews, and analysis must be written first as Cortex sidecars under "
    "notes/system/threads/... via fs(sandbox='cortex'), then referenced as "
    "cortex:notes/system/threads/<file>. Workspace packets are mirrors, not "
    "the primary bus artifact.\n\n"
    "Operations:\n"
    "  fetch   (thread, last?, compact?, mark_read?) — get turns\n"
    "  reply   (thread, to, subject, body, after_turn, from?) — reply; from\n"
    "          defaults to the dispatched role when omitted in tool loops\n"
    "  post    (slug, to, subject, body, from?) — new thread; same default\n"
    "  threads (status?) — list threads; status: active/archived/all\n"
    "  get     (thread, turn_number) — single turn lookup\n\n"
    "arguments MUST be a JSON string or an object.",
    {
        "tool": {
            "type": "string",
            "description": "Operation: fetch, reply, post, threads, get",
        },
        "arguments": {
            "type": "string",
            "description": (
                "JSON string (or object) of operation arguments. "
                'Example: \'{"thread": "480", "last": 3, "compact": true}\''
            ),
        },
    },
    ["tool"],
)


TEAM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    CORTEX_TOOL_DEFINITION,
    _AGENT_BUS_TOOL_DEFINITION,
]

# Static fallback when the live MCP catalog is unreachable. TEAM_TOOL_DEFINITIONS
# is the superset — do NOT concatenate TOOL_DEFINITIONS (duplicate cortex).
STATIC_TOOL_FALLBACK: list[dict[str, Any]] = TEAM_TOOL_DEFINITIONS


# Tool-registry entry: definition + async executor reference name. The
# executor name is resolved by libs/agent_seat/executor.py at tool-loop
# build time — keeps this module free of concrete executor imports
# (avoids agent_seat → executor → tools cycle).
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "cortex": {
        "definition": CORTEX_TOOL_DEFINITION,
        "executor": "cortex_dispatch",
    },
    "agent_bus": {
        "definition": _AGENT_BUS_TOOL_DEFINITION,
        "executor": "agent_bus_dispatch",
    },
    # Safe alias — executor remaps to MCP "web_search" (see executor.py).
    # ¬use "web_search" directly in frontier dispatches: collides with
    # Claude's and Gemini's native search tool name.
    "brave_search": {
        "definition": BRAVE_SEARCH_TOOL_DEFINITION,
        "executor": "brave_search",
    },
}


def resolve_tools(
    names: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve tool names to ``(definitions, executor_names)``.

    Raises ``ValueError`` for unknown names — callers should validate
    against ``TOOL_REGISTRY`` before calling.
    """
    definitions: list[dict[str, Any]] = []
    executors: list[str] = []
    unknown: list[str] = []
    for name in names:
        entry = TOOL_REGISTRY.get(name)
        if entry is None:
            unknown.append(name)
            continue
        definitions.append(entry["definition"])
        executors.append(entry["executor"])
    if unknown:
        raise ValueError(
            f"unknown tool {sorted(set(unknown))!r}; available: {sorted(TOOL_REGISTRY)}"
        )
    return definitions, executors
