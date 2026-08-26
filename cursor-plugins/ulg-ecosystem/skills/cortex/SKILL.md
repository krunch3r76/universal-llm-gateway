---
name: cortex
description: "Tier-2 depth for cortex MCP \u2014 full op catalog, taxonomies, workflow chains, write-path gotchas beyond the in-band tool contract."
---

# Cortex — MCP Tool Depth Reference

**Version:** 1.0 (published 2026-06-30, arc 3782)
**Authority:** Tier-2 depth home for `cortex(...)` dispatch ops. In-band call-time contract stays in the tool docstring; this skill holds relocated reference depth. Where this skill and the tool docstring disagree, **runtime behavior is authoritative** (see Known drifts).

## 1. Purpose + when-to-use

The `cortex` MCP tool is a thin relay to `cortex-api POST /dispatch` — the agent-facing surface for the shared knowledge graph (entities, assertions, structural relationships, session/reasoning edges, journals, todos, tags, friction, reflective journal, audit).

**Load this skill** when you need the full op catalog, derivation/relationship/edge taxonomies, workflow chains, projection semantics (`intent=`), or write-path gotchas beyond what the tool docstring carries in-band.

**Do NOT load for basics** — calling convention, four canonical ops, session boot, channel-routing (when NOT to assert), and claim-shape discipline live in `agent_skill:cortex-orientation`. Read that first on any Cortex session; use this skill for depth.

**Access path invariant:** all Cortex reads/writes route through `cortex(...)` / `cortex_brief(...)` / `local_api(service="cortex-api", ...)`. Never use direct SQL against the cortex DB.

---

## 2. Op catalog (grouped by family)

Each entry: **purpose** · behavioral contract · non-obvious params. Successful responses may carry a `_next` workflow hint.

### Entities

| Op | Purpose · contract · params |
|---|---|
| `entities` | List entities. Filters: `type`, `workflow_state`, `limit`, `query` (case-insensitive substring on `id`/`name`; `%`/`_` escaped), `for_agent` (matches `applicable_agents` JSON list — `*` or slug; missing attr = universal). Prefer `todo_candidates` for routine TODO retrieval. Read-only. |
| `entity_get` | Fetch one entity at a read intent. **`intent="card"`** (preferred for orientation): Card v0 projection-aware fetch (~5–10KB) — identity, `status_summary`, `summary_row`, top-K active assertions (default K=7), `edge_type_summary`, `archives_to_count`, `section_manifest`, `predicate_summary`, freshness. **`intent="full"`**: EntityDetail with active assertions + superseded breadcrumb/corrections. **`intent="full-historical"`**: all superseded rows with full enrichment (audit escape hatch). **`intent="card-md"`**: comprehension-first markdown render (root-only; runtime only — see GAPS). **`intent` ∈ {`cluster`,`impact`}**: reserved → 501. `include_superseded=true` on `full` is legacy alias for `full-historical`. `include_edges` + `edge_limit` (default 20) add reasoning edges. `include_compaction_pointers=true` restores pointer rows filtered from aggregate surfaces. `debug=true` with `card` attaches `fetch_plan_row_volume`. `resolve_aliases` (default true) and `raw_id` (default false) control alias/`merged_into` redirect. See `agent_skill:cortex-v24-implementation-arc` for card semantics. |
| `entity_create` | Create entity. `id` may be `type:slug` or bare slug (server canonicalizes to `type:slug`); mismatched type-prefix → 422. Duplicate id → 409. Option-C traits (`confidence_band`, `lifecycle`, `adoption`) **not** settable at create — derived at birth; set via `entity_update`. `status` synthesized on read — do not pass. `workflow_state` auto-filled to type's `initial_state` when omitted. Auditor-validatability: confirmed-band entities need ≥1 `confidence='confirmed'` assertion citing source (session_close audit-gate flags gaps). |
| `entities_bulk_upsert` | Atomic multi-entity create/update/skip. `if_exists` ∈ {`fail`,`update`,`skip`} (default `fail`); per-item override allowed. |
| `entity_update` | Patch mutable fields including trait write surface (`confidence_band`, `lifecycle`, `adoption`). `source_uri` change recomputes `content_hash`. Same alias resolution params as `entity_get`. |
| `entity_rekey` | Identity-preserving relabel: rewrites child references, seeds old id as alias of new. `new_id` must not exist → 409. Post-commit event `cortex.entity.rekeyed`. |
| `entity_merge` | Fold `source_id` into `target_id` (same type only; cross-type → 422). Dedup-before-repoint; source tombstoned `lifecycle=merged`, `merged_into=target`. |
| `entities_by_content_hash` | Duplicate-detection lookup by content hash. **Requires** `content_hash`; strips `sha256:` prefix before lookup. Default `limit=5`. Optional `type` filter. *(Registry op; absent from tool docstring — see GAPS.)* |

**Entity ID convention:** `{type}:{slug}` — e.g. `person:operator`, `decision:eml-mcp-support`, `todo:slug`. List `entities(type=...)` before creating to avoid duplicates.

### Assertions

| Op | Purpose · contract · params |
|---|---|
| `assertions` | List/filter assertions. Filters: `entity_id`, `entity_id_prefix` (e.g. `service:`), `filter` (claim substring LIKE), `seeded_by`, `confidence`, `review_status`, `superseded`, `limit`. **`intent="summary"`** (default): compact rows + per-row `_deepen`; **`intent="full"`**: enrichment rows. Deepen one row via `assertion_get`. `compact=` is boot-internal — agents use `intent=summary`. |
| `assertion_get` | Single assertion by id (full row incl. `predicate_form`). Deepen target for summary lane. |
| `assertion_state` | Lightweight ratification projection: `{entity_id, ratified, confirmed_count, latest_confirmed_assertion_id}`. Active filter: `confidence='confirmed'` AND `superseded_by IS NULL` (no `review_status` filter). Payload <2KB. Read-only. |
| `assert` | Write assertion. **Not idempotent** — each call mints a row; exact-duplicate active `claim_hash` deduped. `seeded_by` projected seat→family via `agent_seat.seat_to_family`; bare family unchanged; pipeline/unrecognized pass through. Response adds `seeded_by_input`, `seeded_by`, `seeded_by_projection`. `observed_at` auto-fills now if absent. **`valid_from` REQUIRED** when claim contains date pattern unless derivation is observation type → 422 with `valid_derivation_types` hint. Quality routing may set `review_status=staged` (missing `reasoning_summary` on inference, `quality_score < 0.7`, `evidence_uris` without `chunk_id`). **`review_status: null` on clean success** — not "uncommitted"; only promote when explicitly `staged`/`flagged`. `supersedes_id` chains lineage **only** with `force=true`; without force target's `superseded_by` NOT updated (validation_warning) — prefer `supersede` op. `force=true` on contradiction override — last resort. `acknowledge_audit_gaps` suppresses individual confirmed-validatability advisories. Advisory claim brevity warning when `len(claim) > ~300` ∧ ¬`evidence_uris`. **Menu D panel:** pass `attributes=build_panel_assert_attributes(...)` — assertion.attributes is SOT; entity_update cache optional. See `agent_skill:consensus-steelman-posture`. |
| `assertion_update` | Update assertion metadata (`superseded_by`, `valid_until`, `confidence`, `review_status`, `reviewer`, `reviewed_at`, `review_notes`, `predicate_form`; null clears). **Retraction path:** set `valid_until` to now (prefer over supersede for self-seeded noise). Idempotency: setting `superseded_by` when target already superseded → 409 unless `force=true`. |
| `supersede` | Atomic close-old + create-new. Auto-creates `supersedes` edge in same transaction. Unspecified optional fields **inherited** from predecessor (clone-then-override); explicit null drops. `revision_type` ∈ {`restatement`,`correction`,`status_update`} stamps successor `attributes.revision_type`. Idempotency: target already superseded → 409 + rollback unless `force=true`. Same auditor-validatability advisories as `assert`. Prefer over `assert`+manual for belief revision. |
| `analyze_impact` | Semantic pre-write impact analysis (C1). Call before contentious writes. |
| `resolve_assertion_chunk` | Resolve assertion's `chunk_id` to RAG chunk text (URI normalization + verify-on-fetch). |

**Write-safety (cross-op):**
- Exact-duplicate active writes deduped by `claim_hash`
- Near-duplicate may surface `near_duplicate_warning`
- Contradiction detection may 409 — read conflicting assertions before `force`
- Confirmed + inference/user_statement triggers advisory auditor warnings unless acknowledged

**Write-path gotchas (shipped substrate — document here, ¬ rediscover):**
- **`assert` `dry_run=true`** — pre-INSERT validation only. Response: `{dry_run: true, item: null, would_write: true, validation_warnings: [...]}`. **No assertion row.** Auditor / protocol findings are WARN-only (`validation_warnings`); hard-fail only for missing entity (404). `acknowledge_audit_gaps` suppresses named auditor categories on dry_run and write alike. Default `dry_run=false` still persists. Source: `AssertionCreate.dry_run`, `routes/assertions/_create.py`, hermetic `test_assert_dry_run_preflight.py`.
- **`attributes` coerce-both** — `EntityCreate`/`EntityUpdate` and `AssertionCreate`/`AssertionUpdate` run `coerce_attributes_input`: accept `dict` **or** JSON object string; reject non-object / invalid JSON. Entity create/update ValidationError remaps to typed **`422`** `detail={"error":"entity_payload_invalid","diagnostics":[...]}` (`attributes_coerce.entity_payload_validation_exception`). Assertion path surfaces the same coerce via Pydantic before insert.

### Relationships (structural, persistent)

| Op | Purpose · contract · params |
|---|---|
| `relationships` | List active relationships with names, strength. Filters: `entity_id`, `type_id`, `limit`. |
| `relationship_create` | Create structural link. `type_id` from relationship_types registry (see §3). Optional provenance: `session_id`, `agent`. `resolve_aliases` (default true) reports `resolved_aliases`. **`relates_to` is an EDGE type** — use `edge_create`; relationship analogue is `related_to`. |
| `relationships_bulk_upsert` | Atomic multi-relationship create/update/skip. Same `if_exists` semantics as entities bulk. |
| `relationship_update` | Patch mutable fields (`role`, `strength`, `evidence`, `valid_from`, `valid_until`, `source_uri`). Direction/type fixes: delete + recreate. |
| `relationship_delete` | Soft-delete (row preserved; excluded from list). |

### Edges (session/reasoning, session-attributed)

| Op | Purpose · contract · params |
|---|---|
| `edge_create` | Seed reasoning connection. Requires `session_id`, `agent`, `from_node`, `to_node`, `edge_type`. Nodes may be entity ids or `assertion:N` (v2.3 spec — **validator may reject `assertion:N`** per known friction; workaround: edge to parent entity). Optional `strength`, `context` (1–2 sentence reasoning — surfaces in boot narratives). **`relates_to` here is EDGE type** — not `relationship_create`. |
| `edges` | Query edges. Filters: `from_node`, `to_node`, `edge_type`, `agent`, `session_id`, `limit`. |
| `edge_traverse` | Graph traversal 1–2 hops. Filters: `edge_type`, `min_strength`. |
| `edge_retire` | Retire edge (`valid_until`). |
| `edge_update` | Patch `strength`, `context`, `prompt`, `metadata`. *(Registry op; absent from tool docstring.)* |
| `edge_types` | List registered edge types + directionality. **Call before first relationship/edge write** to confirm type ids exist. |
| `impact` | Transitive reverse-dependency BFS from entity. Walks dependency edge types across both substrates. |

**Edge vs relationship:** relationships = persistent structural facts (no session attribution). Session edges = cognitive/reasoning links with session attribution. Do not conflate (see `agent_skill:cortex-provenance-discipline`).

### Journals / session-close

| Op | Purpose · contract · params |
|---|---|
| `journal_read` | Recent session journals. Default `limit=3`; ordering **`id DESC`** (insertion order). Optional `agent` filter (seat-level operational identity). Read-only. |
| `journal_write` | **[DEPRECATED]** — use `session_close`. Auto-creates transcript entity + continues edge. |
| `session_close` | **Atomic** server-side close. Required: `session_id`, `agent`, `session_summary_md`, `summary`. `transcript_depth` ∈ {`verbatim`,`light`,`none`} (default `verbatim`). **`transcript_jsonl_path` or `transcript_md` required iff depth=`verbatim`**. `handoff_prompt`/`handoff_source_path` require depth ∈ {`light`,`verbatim`}; depth=`none` incompatible with handoff. Handoff derivation from HTML comment markers in source file; TOCTOU guards via `expected_*` sha fields → 409 on mismatch. `dry_run` previews without writes. `source_ref` canonicalized server-side; unparseable closes with provenance note (never 422). Response: `journal_row_id`, `transcript_depth`; transcript fields null when depth=`none`. `already_closed` is same-id retry only (persist does **not** auto-hop). Returns 201 with `transcript_entity_id`, `transcript_path`, `content_hash`, `turn_count`, `byte_count` on success. See `agent_skill:session-close`. |
| `session_close_preflight` | Validate args + path sandbox + assemble in memory + audit-gate without writing. Returns `{ok, turn_count, byte_count, audit, warnings}` or `{ok:false, error, reason}`. When the supplied or JSONL-start id is already journaled **and** a later user `<timestamp>` exists: `ok=true` with successor `session_id`, `prior_session_id`, `hop_reason=session_id_already_journaled`. Run before committing close. |
| `session_handoff_upsert` | Upsert `handoff_prompt` on **already-closed** session. 404 if not closed. Idempotent replace. Boot omits handoffs — explicit retrieval via `entity_get(transcript:{session_id})` or journal row. |
| `assemble_transcript` | Debug/probe: verbatim layer from Cursor JSONL only. **NOT** used in close path. |

**Large payload discipline:** for quote-heavy `session_summary_md` / handoff bodies, use file-path params (`transcript_jsonl_path`, `handoff_source_path`, `source_ref`) — do not hand-build escaped JSON strings.

### Reflective journal (`rj_*`)

| Op | Purpose · contract · params |
|---|---|
| `rj_write` | Write reflective journal entry. `kind` default `entry`; also `reflection`, `revision`, `consolidation`. **`kind="handoff"` retired** as direct agent write — forward narratives via `session_close(handoff_prompt=...)` only. |
| `rj_read` | Fetch one entry with links. |
| `rj_list` | List entries. Filters: `agent`, `kind`, `limit`, `offset`. |
| `rj_link` | Link entry to another entry or entity. |
| `rj_consolidate` | Write consolidation entry (`kind=consolidation`); requires `throughline`, `before`, `now`; optional `tension_points`, `contradiction_set`, `falsifier`, `source_entry_ids`. |

Boot surfaces RJ for epistemic shifts (`reflection`, `consolidation`), not operational kickoff imperatives.

### Deadlines

| Op | Purpose · contract · params |
|---|---|
| `deadlines` | List active deadlines. Read-only. |
| `deadline_resolve` | Atomic two-write close: confirmed RESOLVED assertion + `outcome:met` on attributes. Eliminates ghost-deadline boot failures from forgetting second write. Optional `fulfilling_assertion_id`. |

### Todos

Todos are entities `type: "todo"` with `workflow_state`: `open` | `in_progress` | `blocked` | `done` | `deferred` | `cancelled`.

| Op | Purpose · contract · params |
|---|---|
| `todo_candidates` | Ranked TODO retrieval for user intent — **prefer over** broad `entities(type=todo, workflow_state=open)`. Filters: `q`/`query`, `limit`, `workflow_state`, `priority`, `domain`, `domain_exclude`, `context`. |
| `todo_audit` | Stale/open TODO audit for deferral, closure, merge, spec conversion. Filters: `stale_days`, `limit`, `domain`, `priority`. |

**Closure (preferred):** `pipeline(op="run", pipeline_id="todo-close", ...)`.
**Fallback:** `entity_update(workflow_state="done")` + manual closure assertion.

*(Registry sidecar ops not in tool docstring: `todo_close_sidecar`, `todo_distill_implement_gate` — see GAPS.)*

### Search / activate / resolve

| Op | Purpose · contract · params |
|---|---|
| `search` | Hybrid **FTS5 + vector**, CombMAX fusion (`search_mode` in response). Over assertions. **`intent="summary"`** (default): compact hits — omits `session_tag` and `evidence` by design. **`intent="full"`**: enrichment rows. Filters: `superseded`, `entity_type`. Prefer over `assertions` for natural-language queries. |
| `activate` | Spreading activation from seed entities. `entity_ids` comma-separated. Params: `depth`, `max_results`, `exclude_ids`, `suppress_hubs`, `decay_factor`. Returns entrenchment-weighted activated assertions across association edge set. |
| `resolve` | Resolve `cortex://` URI. |
| `surface_forms` | Entity mention resolution cache. Filters: `entity_id`, `mention`, `mention_type`, `limit`. |

**Retrieval workflow:** `search` → extract entity_ids → `activate` → rank by `entrenchment_score` → `tag_resolve` for pinned `current` assertions. Belief revision: `supersede`, never assert alongside stale claim.

### Subgraph materialization

| Op | Purpose · contract · params |
|---|---|
| `walk_subgraph` | Lean structural topology without assertion canvas. Params: `root`, `hops`, `edge_types`, `direction`, `entity_cap` (default 200), `include_counts`, `promote_hubs`, `hub_rel_threshold`. Prefer for hub/orientation navigation. *(Absent from tool docstring; present in `cortex-essentials.mdc`.)* |
| `render_subgraph` | Deterministic markdown canvas for session-open materialization. Params: `root`, `hops`, `top_k_assertions`, `include_superseded`, `edge_types`, `neighbor_fidelity` (`depth_aware` default; `full` for hygiene diagnostics). Hard cap **50 entities** → `subgraph_too_large` 422. Size gate ≤40KB. Full guidance: `agent_skill:subgraph-render`. |

**When NOT:** single entity → `entity_get(intent=card)`; NL search → `search`; reasoning-edge traversal → `edge_traverse`.

### Friction / observe

| Op | Purpose · contract · params |
|---|---|
| `observe` | Lightweight observation. Defaults: `entity_id=person:operator`, `confidence=believed`, `derivation_type=agent_observation`. |
| `friction` | Log tool/schema/boot **or feature-ask** friction — **observation only, NOT ticket channel**. `owner`: bare slug → `service:{slug}` or full `service:`/`agent_skill:`/`ai_agent:` id (non-service owners must exist). `service=` back-compat alias. Optional `confidence` (`confirmed`/`believed`/`suspected`/`hypothesized`) is **honoured**; invalid values are rejected (never silently downgraded). Omitted → `hypothesized`/0.5. Categories: `tool_error`, `tool_mismatch`, `tool_absent`, `schema_gap`, `boot_drift`, `lesson_gap`, `lesson_conflict`, `stale_context`, `doc_drift`, `protocol`, `regression`, `feature`. `feature` defaults `actionable=false` + `defer_enqueue=true` (no auto-todo). Actionable gap rows → codified bug cycle (investigate → execute); see `agent_skill:friction-review`. |
| `frictions` | List open friction observations across owner types. Default limit=7. `intent=summary|full`. Filters: `owner`, `owner_type`, `category`, `seeded_by`, `superseded`. |
| `friction_close` | Close friction after fix. Creates closure assertion (`derivation_type=commitment`, `confidence=confirmed`) + `resolves` edge via supersede. **`resolution_kind`:** `agent_skill:{slug}`, `workflow:{slug}`, `todo:{slug}`, `commit:{sha}`, `superseded`, `wontfix`. May auto-promote to recon-pending todo when `resolution_kind=todo:{slug}`. *(In `cortex-deep-ref.mdc`; absent from tool docstring.)* |

### Tags

| Op | Purpose · contract · params |
|---|---|
| `tag_assign` | Assign/move named tag on entity to assertion. Requires `tag_name`, `entity_id`, `assertion_id`, `agent`. |
| `tag_list` | List tags for entity. |
| `tag_resolve` | Resolve tag to assertion (e.g. pinned `current`). |

### Audit / stats / review

| Op | Purpose · contract · params |
|---|---|
| `stats` | Dashboard counts. `stats.assertions` reports `total = active_content + compaction_pointers` separately. Read-only. |
| `review_queue` | Provisional entities + flagged assertions + staged assertions bucket (priority 1) + low-confidence + thin descriptions. |
| `audit` | Run gap detectors (graph-only default). `include_filesystem=true` adds fs checks. Returns findings + counts. Phase 1b projection/audit primitives. |

*(Registry review ops not in tool docstring: `session_audit`, `case_audit`, `fill_gaps`, `prose_fact_scan` — see GAPS.)*

### Composite / admission ops (registry only)

These exist in `dispatch_ops/_OPS` but are **not** documented in the primary tool docstring. Treat as internal/admission surfaces unless your workflow explicitly references them:

| Op | Notes |
|---|---|
| `implement_ready_preflight` | Non-writing preflight for todo-sourced implement dispatch. Requires `source_ref`. |
| `doc_template` | Document template helper. |
| `doc_validate` | Delegates to `preflight_implement_ready`; emits attestation tokens on pass. |
| `register_skill_substrate` | Skill registration composite. |
| `thread_sidecar_write` | Thread sidecar persistence. |
| `recon_sidecar_write` | Recon sidecar persistence. |
| `pinned_deliverable_write` | Pinned deliverable persistence. |

---

## 3. Taxonomies

### derivation_types

Returned inline in 422 bodies as `valid_derivation_types`. Co-requirements enforced at write time:

| Value | Meaning | Requires |
|---|---|---|
| `inference` | Agent synthesis from prior context/reasoning | — (`reasoning_summary` strongly recommended; omission may route to `staged`) |
| `user_statement` | User told you directly | — |
| `agent_observation` | Tool output / runtime behavior | — |
| `direct_observation` | Deterministic structural read (schema, fs, config) | — |
| `compression` | Compressed from ingested document chunks | `chunk_id` + `evidence_uris` |
| `thread_compression` | Thread compaction summary from workspace artifacts | `evidence_uris` (no `chunk_id`) |
| `quotation` | Verbatim quote from ingested chunk | `chunk_id` + `evidence_uris` |
| `commitment` | Promise/commitment (incl. friction_close resolutions) | — |
| `stated` | Stated claim (less structured than user_statement) | — |
| `other` | None of the above | `reasoning_summary` strongly recommended |

**Date-pattern invariant:** claim containing YYYY-MM-DD, ISO timestamp, or named date → **`valid_from` required** (unless observation derivation types exempt per 422 hint).

**Session-synthesized summaries** (compaction, restructure consolidation): use `derivation_type=inference` + non-empty `reasoning_summary` — **NOT** `compression` (reserved for ingested chunks).

### relationship_types (registry)

Valid `type_id` values for `relationship_create` (from tool docstring — call `relationships` or confirm before write):

`agent_under_poa_for`, `amends`, `assessed_on`, `associated_with`, `belongs_to`, `beneficiary_of`, `blocked_by`, `causes`, `child_of`, `co_occurs`, `co_owns`, `contradicts`, `controlled_account_of`, `correspondence_with`, `deadline_for`, `depends_on`, `depicts`, `elaborates`, `employed_by`, `enables`, `evidence_for`, `filed_against`, `filed_by`, `filed_in`, `heir_of`, `involves`, `issued_by`, `location_of`, `mentor_of`, `object_of`, `org_party_to`, `owns`, `parent_of`, `participant`, `party_to`, `payment_on`, `personal_representative_of`, `pertains_to`, `preceded_by`, `precedes`, `recipient_of`, `references`, `refused_to_provide_to`, `related_to`, `represents`, `requested_records_from`, `requires`, `respondent_in`, `responds_to`, `retained_by`, `role_in`, `secured_by`, `sibling_of`, `subject_of`, `succeeded_by`, `succeeds`, `supersedes`, `supplement_to`, `transferred_from`, `transferred_to`, `triggers`, `trustee_of`.

**Naming trap:** `relates_to` is an **edge** type (`edge_create`), not a relationship type. Relationship analogue: `related_to`.

### edge_types (reasoning/session)

Common types (full list via `edge_types()`):

`depends_on`, `leads_to`, `caused_by`, `contradicts`, `supersedes`, `relates_to`, `evidence_for`, `corroborates`, `derived_from`, `extends`, `promises`, `expects`, `continues`, `analogous_to`, `reasoned_about`.

Session-close and belief-revision flows auto-create some edges (e.g. `supersedes` on supersede op, `continues` on close).

### confidence ladder

| Level | Meaning |
|---|---|
| `confirmed` | Verified fact or settled decision |
| `believed` | Working assumption, high confidence |
| `suspected` | Pattern-based inference, not yet verified |
| `hypothesized` | Theory under investigation |

`confirmed` triggers auditor-validatability advisories on `assert`/`supersede` unless `acknowledge_audit_gaps` set. See `agent_skill:auditor-validatable-confidence`.

### review_status

| Value | Meaning |
|---|---|
| `committed` | Fully committed (explicit on some routes; `supersede` default) |
| `staged` | Quality-gate routed — needs graduation |
| `flagged` | Needs human/agent review |
| `rejected` | Rejected |

**Staging triggers:** missing `reasoning_summary` on inference, `evidence_uris` without `chunk_id`, `quality_score < 0.7`.

**Graduation:** add `reasoning_summary`/`chunk_id` then `assertion_update(review_status="committed")`, or aging policy (30d commit if `confidence_score>=0.7`).

**Asymmetry:** `assert` returns `review_status: null` on clean success (post-validation OK). `supersede` writes explicit `committed`. Both mean active for reads — do not auto-promote null rows.

---

## 4. Workflow chains + examples

### Entity seeding chain

```
entity_create → assert → relationship_create → entity_get(intent=card)
```

Verify entity exists and relationships wired before dependent writes.

### Belief revision chain

```
supersede(old_assertion_id=..., ...) → entity_get → confirm old row shows superseded_by, new row visible
```

Never `assert` a correction alongside an active contradictory row. For self-seeded noise, **retract** (`assertion_update valid_until`) rather than supersede.

### Session close chain

```
session_close_preflight (optional) → session_close → quote response IDs (transcript_entity_id, content_hash, journal_row_id) → assert on relevant entities using transcript as evidence_uri → activity journal (thread 480) → STOP (no read-back verification fetch)
```

Workflow hint on success: capture `transcript_entity_id` AND `content_hash` from response; quote hash in user-facing completion line.

**Depth selection:** verbatim (default) requires transcript source; light = structural file only; none = journal + continues edge only (no transcript entity).

**Deprecated path:** `journal_write` → use `session_close`.

### Retrieval chain

```
search(query=...) → activate(entity_ids=...) → tag_resolve(tag_name="current", ...) → entity_get(intent=card)
```

For personal facts: search first, check temporal bounds (`valid_from`/`valid_until`), scan supersession chains.

### Friction → fix chain

```
friction(owner=..., category=..., note=...) → [investigate via consult] → fix → friction_close(assertion_id=..., resolution_kind="agent_skill:..." | "todo:..." | ...)
```

`friction()` alone does not open a fix cycle.

### Panel / Menu D assert chain

```
panel_dispatch(...) → lead adjudication → assert(..., attributes=build_panel_assert_attributes(...), evidence_uris=[agent-bus:T, execution:E, ...])
```

Assertion.attributes is SOT for consensus disposition; session-close runs `panel_disposition_incomplete` detector. See `agent_skill:consensus-steelman-posture`.

### Subgraph session-open chain

```
walk_subgraph(root=..., hops=1)           # orientation / topology
render_subgraph(root=..., hops=1, top_k_assertions=10)  # assertion canvas
```

### Staged assertion graduation

```
review_queue → assertions(review_status=staged) → assertion_update(review_status=committed) after adding reasoning_summary
```

Scripts: `scripts/cortex/triage-staged.py`, `scripts/cortex/age-staged.py`.

### Examples

```
cortex(tool="todo_candidates", arguments='{"query": "cortex retrieval", "limit": 5}')
cortex(tool="entity_get", arguments='{"entity_id": "decision:my-slug", "intent": "card"}')
cortex(tool="search", arguments='{"query": "embedding \\"recall\\" tradeoffs", "limit": 10}')
cortex(tool="assert", arguments='{"entity_id": "decision:my-slug", "claim": "...", "confidence": "confirmed", "evidence": "...", "derivation_type": "inference", "reasoning_summary": "..."}')
cortex(tool="supersede", arguments='{"old_assertion_id": 4, "entity_id": "person:foo", "claim": "...", "confidence": "confirmed", "evidence": "...", "session_id": "claude-cursor-2026-06-30-1200", "agent": "claude-cursor"}')
cortex(tool="friction_close", arguments='{"assertion_id": 1234, "resolution_kind": "agent_skill:pre-deploy-gate-discipline", "agent": "cursor"}')
```

---

## 5. Cross-refs (pointers only)

| Skill | Load when |
|---|---|
| `agent_skill:cortex-orientation` | Every Cortex session — calling convention, boot, four canonical ops, channel routing, claim shape |
| `agent_skill:cortex-provenance-discipline` | Citing Cortex substrate in derived artifacts — `[assertion:NNNN]` grammar, reader-defense |
| `agent_skill:cortex-entity-restructure` | Splitting overloaded entities, assertion migration, compaction pointers |
| `agent_skill:cortex-v24-implementation-arc` | `intent=card`, predicate_form/summary, compaction pointer reads, tier ladder |
| `agent_skill:subgraph-render` | Before any `render_subgraph` call |
| `agent_skill:session-close` | Full close protocol (Cursor + web/kernel variants) |
| `agent_skill:auditor-validatable-confidence` | Confirmed-band writes, audit-gate findings |
| `agent_skill:consensus-steelman-posture` | Menu D panel asserts, `build_panel_assert_attributes` |
| `agent_skill:friction-review` | Triage before friction/todo/bus routing |

**Operational gotchas corpus:** `entity_get(entity_id="service:cortex", intent="card")` — 112+ active operational notes ranked by entrenchment.

**Service topology:** UDS `/tmp/universal-protocol/cortex-api.sock`; library `libs/cortex_store/`; DB `~/.cortex/cortex.db`; lifecycle via `manage(action="restart", service="cortex_api")`.

---

## Known doc-vs-runtime drifts (reconcile targets)

Reader guidance: **runtime is authoritative** for behavior; each item below is a docstring/sibling-doc reconcile target feeding the descriptor-slim reconciliation track. Do not treat the drifted docstring text as contract.

1. **`entity_get` default `intent`:** Tool docstring (`cortex.py:39`) states `intent="full" (default)`. Runtime dispatch (`ops_entities.py:_op_entity_get`) defaults `intent="card"`. **Runtime wins:** assume `card` unless you pass `intent` explicitly; the docstring is the reconcile target.

2. **`cortex_brief` parameter shape:** `cortex-essentials.mdc` / `cortex-deep-ref.mdc` use `cortex_brief(agent="cursor")`. Injected `cortex-orientation` uses `cortex_brief(family="claude", platform="cursor")`. `tool-reference.md` §cortex_brief documents `family`+`platform` with default `claude-cursor`. **Boot is a separate MCP tool** — not in this skill's op catalog; conflict noted for orientation cross-ref.

3. **`assert` `derivation_type` enumeration:** `tool-reference.md` lists only `quotation/compression/inference/other`. Tool docstring + `assertion_quality.py` list full taxonomy incl. `thread_compression`, `commitment`, `stated`, `user_statement`, `agent_observation`, `direct_observation`. **Prefer runtime taxonomy (§3 above).**

4. **`journal_write` params:** `tool-reference.md` documents `file_path`; tool docstring documents `markdown_content` (and deprecated status). **Unclear which is current dispatch surface.**

5. **`entity_create` `content_hash`:** `tool-reference.md` lists `content_hash?` on create; tool docstring omits it (mentions `source_uri` auto-hash on update only).

6. **`relationship_create` extended params:** `tool-reference.md` lists `chunk_id`, `valid_from`, `valid_until`, `source_uri`; tool docstring lists shorter param set (adds `resolve_aliases`).

7. **`edge_create` extended params:** `tool-reference.md` lists `edge_source`, `prompt`, `seeded_by`, `metadata`; tool docstring lists shorter set (`context` only in optional).

8. **`edges` `include_retired`:** Present in `tool-reference.md`; absent from tool docstring.

9. **Channel routing session narrative:** `cortex-essentials.mdc` routes session work to `journal_write`; injected `cortex-orientation` routes to `session transcript + session_close`. **Prefer orientation (session_close is canonical).**

10. **`observe` default entity:** `cortex-deep-ref.mdc` examples omit default; tool docstring implies `person:operator` default — confirm at call time.

---

## GAPS[]

Depth present in runtime/registry or sibling docs but **missing or incomplete in primary tool docstring**:

1. **`walk_subgraph`** — full op in registry + `cortex-essentials.mdc`; not in tool docstring op table.
2. **`friction_close`** — in registry + `cortex-deep-ref.mdc`; not in tool docstring.
3. **`entities_by_content_hash`** — registry op; mentioned only in descriptor-authoring-discipline EXTRACT list.
4. **`entity_get` `intent="card-md"`** — supported in runtime (`ops_entities.py`); not in tool docstring.
5. **`edge_update`** — registry op; no agent-facing docstring entry.
6. **Review/admission ops:** `session_audit`, `case_audit`, `fill_gaps`, `prose_fact_scan`, `implement_ready_preflight`, `doc_template`, `doc_validate`, `register_skill_substrate` — registry only.
7. **Sidecar write ops:** `thread_sidecar_write`, `recon_sidecar_write`, `pinned_deliverable_write`, `todo_close_sidecar`, `todo_distill_implement_gate` — registry only.
8. **`render_subgraph` params:** runtime supports `neighbor_fidelity`, `hub_rel_threshold`; tool docstring lists shorter param set.
9. **`tool-reference.md` §cortex** — covers ~20 ops only; large subset of docstring ops absent (session_close family, todos, tags, friction, rj_*, audit, activate, etc.). Treat docstring as primary; tool-ref as partial mirror with conflicts noted above.
10. **Panel assert `attributes` schema** — behavioral detail lives in `consensus-steelman-posture` + `_oc_surface_templates.py`, not in cortex op docstring body beyond one clause.

---

*Published 2026-06-30 (arc 3782, `mcp-descriptor-slim`). The 31-op `canonical.yaml` fol_descriptor slim and the per-tool docstring distillation reference this skill via `See agent_skill:cortex` pointers — do not slim those surfaces past their Tier-1 call-time contract without confirming the relocated depth lands here.*
