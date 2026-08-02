---
trigger_match_terms: ["cortex-v24-implementation-arc", "cortex_v24_implementation_arc", "v2.4", "implementation", "slice", "cortex-planning", "living", "working-notebook", "skill", "arc.", "planning", "dispatc"]
---

# Cortex v2.4 — Implementation Arc

**Version:** 0.3
**Last updated:** 2026-05-07
**Authority level:** WORKING-NOTEBOOK for the v2.4 implementation arc. NOT canonical.
**Sunset:** Retire this file when Phase 3 (neighbor expansion) ships. Fold any still-relevant content into the v2.4 spec proper as completion notes; delete this file. Do not let this skill ossify into doctrine.

**Canonical spec:** `documents/specs/cortex-v2.4-read-model-architecture.md`
**Substrate spec:** `documents/specs/cortex-v2.3-session-edges.md` (the reasoning-edge graph v2.4 reads over)

**Companion to:**
- `cortex-entity-restructure` (graph-cleanup discipline; relevant when v2.4 reads expose entity-shape problems)
- `advisor-timing.mdc` (always-applied workspace rule — read-only plan pass before any v2.4 slice)
- `pre-deploy-gate-discipline` (when a v2.4 slice touches the live read path)

---

## When to read this skill

Read this skill — in full — before doing any of the following:

- Planning or scoping a v2.4 implementation slice
- Dispatching v2.4 implementation work to any executor
- Reviewing a PR that touches `entity_get`, `assertions()`, `boot/recent_mentions`, `list_assertions`, `search_assertions`, `review_queue`, the multi-representation projector, or any read-path surface in `cortex-api`
- Writing or scoring an architectural decision involving `intent=`, `card v0`, `predicate_form`, `predicate_summary`, π reranking, neighbor expansion, the cognitive cache, the tier ladder, or compaction-pointer read semantics
- Resuming any v2.4 implementation slice

If the work is *not* v2.4 implementation — e.g. analyzing a Cortex graph, restructuring an entity, debugging a single read — read the canonical spec section directly. This file is for the in-flight build, not for understanding the architecture.

---

## Where we are (shipped)

### Mechanism layer — both faces (2026-05-03)

The v2.4 mechanism layer for compaction-pointer reads is complete on both surfaces. The cognitive layer is not started.

**Per-entity face (§6.10)** — assertion 8212, session `web-2026-05-03-1739` planned, Cursor session `cursor-2026-05-03-...` shipped:
- `entity_get` and `assertions(entity_id=...)` deprioritize-not-omit pointer rows
- Tombstone-only entities collapse to consolidation summary with `archived → see children [X, Y, Z]` hint derived from `archives_to` edges
- `include_compaction_pointers=true` flag for structural audit

**Aggregate face** — assertion 8267 (cohort with 8261), Cursor session `cursor-2026-05-03-1406`, ratified by thread 873:
- `boot/recent_mentions`, `list_assertions(entity_id=None)`, `search_assertions`, `review_queue` strict-exclude pointer rows
- `include_compaction_pointers` query param restores prior unfiltered behavior on every surface
- `stats.assertions` now reports `total = active_content + compaction_pointers` separately
- New module: `libs/cortex_store/compaction.py` — `POINTER_SQL_LIKE` constant + `filter_compaction_pointers()` strict-exclude (sibling to existing deprioritize-not-omit `apply_compaction_filter()`)
- 29/29 tests passing in `libs/cortex_store/tests/test_compaction.py`

**Discriminator choice:** Option A (claim-text LIKE pattern `"Compacted into archive summary %"`), per `tasks/specs/cortex-aggregate-compaction-filter.md` §3 recommendation. Option B (typed flag) deferred to ride along with §4 auto-maintenance when both land.

### Phase 1 — Slice 1 (CLOSED, 2026-05-07)

**HEAD:** `221abba4` (initial) + review-fix commit
**Review:** web-anthropic, agent-bus thread 899 — all Criticals + Warnings + Suggestions addressed.

**Delivered:**
- `intent: Literal["full","card","cluster","impact"]` param on `entity_get` route, dispatch op, MCP tool, models
- Projection-aware fetch plan for `intent="card"`: separate query path — identity + top-K active assertions + edge-type aggregates + archives_to count + session-edge count. NOT load-and-trim.
- Card v0 Pydantic model (`EntityCard`): identity, status_summary, summary_row, top_k_assertions, edge_type_summary, archives_to_count, section_manifest, predicate_summary, freshness, debug
- `?debug=1` exposes `fetch_plan_row_volume` for §7.7 architectural-honesty test
- Card SQL LIKE clauses parametrized via canonical `POINTER_SQL_LIKE` / `SUMMARY_SQL_LIKE` from `compaction.py`
- `predicate_summary` slot populated via `synthesize_predicate_summary()` heuristic (deterministic, no LLM)
- Tombstone-collapse branch: all-pointer active assertions → nav-hint predicate_summary
- Default `intent="full"` — opt-in, no caller migration

**Empirical verification:** card-debug vs full against `person:operator` — ≈300× byte reduction, ≈21× row reduction. §6.2 architectural target met.

### Phase 1 — Slice 2 (CLOSED, 2026-05-07)

**HEAD:** `07de800a` (Slice 2) + `03796419` (entity_status import fix) + `a87a40a7` (xAI remote MCP guard)
**Review:** web-anthropic peer review, agent-bus thread 902 — A+ remediation applied.

**Delivered:**
- `libs/cortex_store/card.py` — card-mode read path extracted from `routes/entities.py`
- `libs/cortex_store/card_adapters/` — per-entity-type adapters (`todo`, `decision`, `document`, `service`, `case`, `person`, `default`); each declares `expected_section_ids: ClassVar[tuple]`
- `libs/cortex_store/entity_crud.py` — CRUD impls + relationship SQL constants extracted from `routes/entities.py`
- `libs/cortex_store/test_intent_card.py` — 20 Layer-2 integration tests (all pass)
- `EntityCard.predicate_summary` narrowed `str | None = None` → `str = ""` (schema/docstring drift fix)
- `DecisionAdapter` dead-equivalent `status_summary` override removed

**§6.4 stance (provisional):** uniform section-id set across all adapters — plausible-minimum reading; per-adapter `expected_section_ids` ClassVar binds contract per-type so future divergence is one-line edit. Tracked in `decision:cortex-v24-card-section-uniformity` (assertion 8520) with 3 action-shaped path-forcing conditions; closure-gap fires at **Slice 4 preamble**.

**SLOC waiver:** `entity_crud.py` (422 SLOC) and `test_intent_card.py` (482 SLOC) exceed 300-SLOC new-file gate. Waiver granted. **Slice 3 preamble MUST split both files before any Slice 3 work proceeds** (assertion 8521 on `spec:cortex-v2.4`). Natural splits documented in that assertion.

**Cleanup todos (carried-forward, low priority, separate pass):**
- `todo:cortex-routes-entities-501-envelope-conformance` — 501 envelope conformance
- `todo:cortex-store-bare-logger-to-emit-event` — migrate `cortex_store` from stdlib logging to `universal_logging` + `emit_event`

---

## Where we're going

### Phase 1 — Slice 3: `predicate_form` storage + projector wiring

**Preamble gate (mandatory before any Slice 3 work):** split `entity_crud.py` and `test_intent_card.py` per the SLOC waiver (assertion 8521 on `spec:cortex-v2.4`). Natural splits documented in that assertion. Do not open Slice 3 without confirming this split is done.

**Design decisions locked:**
1. Storage: column on `assertions` (§6.7 peer field) — migration adds column; post-write hook fires projector async
2. Dispatch: fire-and-forget pipeline `pipelines/predicate_extract/`
3. Tier-2 model `qwen3-14b-q4-k-m-40960` per arc skill

Cache keyed `(assertion_id, content_hash, edge_state_hash)` per §5.5.4.

**§6.4 check:** if `predicate_form` extraction surfaces per-type structural variation that uniform sections cannot express, revisit §6.4 stance early (path-forcing condition 3 on assertion 8520).

### Phase 1 — Slice 4: `predicate_summary` aggregation + heuristic promotion
**Preamble gate:** RATIFIED 2026-05-07 (thread 907). §6.4 uniform section-id stance folded provisional → final. See ratification assertion on `decision:cortex-v24-card-section-uniformity`. Per-adapter `expected_section_ids` ClassVar binding remains as the structural seam for any future per-type divergence; aggregation produces one string regardless of host type.

**Spec refinements** (thread 907 turn 2, all Suggestion severity, applied):
- Tier 1 cap-at-1: sync enrichment caps at AT MOST ONE missing assertion per read; rest fall through to Tier 2 / async post-write. Protects card-read latency.
- Tier 0 join order: assertion order from card top-K query (summary-first, pointer-last, created_at DESC). Contract-stable for §5.5.4 cognitive cache hashing.
- §6.7 deterministic-rule-extraction-over-assertion-text deferred to Phase 2 / Phase 5 (separate assertion on `spec:cortex-v2.4`). Tier 2 stays edge-only as cost-bounded fallback.

Tier-0 mechanical aggregate when all top-K have `predicate_form`; Tier-1 opportunistic sync (capped at 1) when projector available; Tier-2 heuristic synthesis (already shipped in Slice 1, edge-only) remains as permanent fallback. Closes §3.3 / §6.7 / §7.1 contract for Phase 1.

### Phase 2 — Predicate enrichment + backfill

Scheduling discipline for historical assertions. Hybrid: lazy-on-read + cron batch backfill ordered by `drift signal × entity value`. Same projector, same cognitive cache.

### Phase 3 — Neighbor expansion

`include_neighbors` opt-in, defaults `false`. `neighbor_limit` default 3, **hard cap 5** (architectural). Deterministic strong-neighbor selection per §5. Composition of existing per-entity cards.

### Phase 4 — Templated view maintenance (deferred)

Write-side automation of §4.1 rule. Not on v2.4 critical path.

### Phase 5 — Cognitive maintenance (deferred)

Full §5.5 review surface — production loop + quality loop via Tier 0–6 ladder. Wires to `plan:periodic-cortex-consolidation` Dream Architecture.

---

## Architectural invariants that bear weight

These five are the invariants that, if violated under deadline pressure, quietly turn v2.4 into "v2.3 with extra steps." Anyone scoping a v2.4 slice should be able to recite them:

1. **Cognition where it matters; mechanism where it suffices.** (§5.5.2) Local-LLM cognition is the *producer* of cards, predicates, and π scores — not a corrective layer above prior heuristics. Mechanism handles deterministic operations (hash compare, edge counting, archive-pointer materialization). The §6.10 mechanism layer we just shipped is the canonical example: deterministic projection of compaction pointers, no model in the read path.

2. **Projection as fetch-plan choice, not serialization choice.** (§6.2) `intent="card"` must execute a projection-aware query plan that loads only the fields/rows needed for the card payload. "Load the full 221KB entity, then trim the response" is a *named anti-pattern* — it satisfies byte tests and fails the architectural one. Test plan §7.7 includes a fetch-plan-row-volume assertion specifically to catch this.

3. **Convergence gate on relationships↔edges remains closed.** (v2.3 §10.5) Four criteria must ALL hold before merging the tables. None do. v2.4 reads over both layers; a convergence pass right now would invalidate the §5 reranker signals.

4. **`predicate_summary` slot reserved from Phase 1.** (§3.3, §6.3, §6.7) The slot is in the Card v0 payload from day one — populated via local-LLM `predicate_form` aggregation when available, edge-derived heuristic synthesis when not. The contract stabilizes once. Treating the slot as deferred-to-Phase-2 forces a second payload-shape break and is wrong.

5. **DRY via content-addressable cognitive cache.** (§5.5.4) Hash the substrate (assertion ids + content hashes + edge state + intent). Cache cognitive output against that hash. Re-run only when substrate changes. Without this, cognitive maintenance recomputes from raw assertions every cycle and §5.5 becomes affordability-prohibitive.

---

## Tier ladder — operational cheat sheet

Compressing §5.5.6 for working memory. **Always query `model_status(model_id=...)` at decision time** — effective per-slot context (`total / N_slots`) is the operative budget, not advertised max-context.

| Tier | Models (today) | Latency | Used for |
|---|---|---|---|
| T0 | — (cache hit) | free | substrate-hash matches; cached artifact returns |
| T1 | `qwen3-1-7b-q8-0-40960` (~10K/slot, 4 slots) or `phi-3-5-mini-instruct-q8-0-65536` (~8K/slot, 8 slots) | sub-second | cache decisions, intent-conditioning context, lightweight scans |
| T2 | `qwen3-14b-q4-k-m-40960` (~8K/slot, 5 slots) — concurrent default; `qwen3-14b-q4-k-m-32768` (32K/slot, 1) or `ministral-3-8b-instruct-2512-q6-k-131072` (131K/slot, 1) — long-context fallback | seconds | predicate extraction (per-assertion `predicate_form`) |
| T3 | `baai-bge-reranker-v2-m3` (cross-encoder, no per-slot constraint) + `gemma-4-26b-a4b-it-q4-k-m-65536` (preferred, on Jupiter) or `qwen3-5-27b-q8-0-65536` (alt) | single-digit seconds | π reranking, intent-conditioned card top-K |
| T4 | `hermes-3-llama-3-1-70b-uncensored-q4-k-m-{131072,32768}-hybrid` | tens of seconds | quality audit, hard predicate cases. Hybrid CPU+GPU offload — latency variance higher, effective per-slot context not exposed via `model_status`; runtime measure before context-heavy use. |
| T5 | `openai/gpt-5.4`, `claude-sonnet-4` | network | exception path on observed local insufficiency. **Personal-data scopes (HEI, Chase, BOE-19-P, estate, mortgage) require explicit policy gate.** |
| T6 | `openai/gpt-5.5`, `claude-opus-4` | network | architecture-implicating drift surfaced by T4 audit only. Rare. |

Production work runs T1–T3 by default; quality audit T4; cloud is exception. Local-first keeps personal data inside the trust boundary and removes per-token cost from the read path.

---

## Anti-patterns specific to v2.4

1. **Load-then-trim card mode.** Building `intent="card"` as a serializer that reads the full entity and prunes fields. Violates §6.2. Test for it: card mode must show smaller `fetch_plan_row_volume` than `full` mode, not just smaller wire bytes.

2. **Linear weighting on π.** Reaching for `α·structural + β·temporal + γ·...` when scoring Stage-2 candidates. §5 closed Open Q4 *by composition* — cross-encoder reranker conditioned on intent. Adding a linear tuning function reintroduces the problem the reranker is solving.

3. **Deferring `predicate_summary` to Phase 2.** Treating Phase 1 as "ship cards, populate predicate_summary later." Per §6.7, the slot is populated from Phase 1 onward via local-LLM-or-heuristic-fallback. The contract stabilizes up front so callers don't need a second payload-shape migration.

4. **`derivation_type="compression"` for session-synthesized summaries.** That value is reserved for ingested-document chunks and requires `chunk_id` + non-empty `evidence_uris`. For consolidation summaries written during compaction or restructure, use `derivation_type="inference"` with a non-empty `reasoning_summary`.

5. **Treating `assertion:N` as session-edge-only.** v2.3 §3.4 explicitly lists `"assertion:494"` as a valid node address. But the `edge_create` endpoint validator currently rejects `assertion:N` with `dangling_edge` (empirically confirmed session `web-2026-05-03-2304`). Workaround: capture provenance via reasoning edge to the assertion's *parent entity*.

6. **Cloud-tier escalation by default.** Reaching for `gpt-5.4` / Sonnet / Opus on routine production work. Cloud is the gated exception path on observed local insufficiency, not the convenience default.

7. **Inlining compaction SQL patterns.** Writing LIKE predicates for pointer/summary discrimination directly in route code instead of importing `POINTER_SQL_LIKE` / `SUMMARY_SQL_LIKE` from `compaction.py`. This was the Critical Code #1 finding in thread 899. ∀ card path or aggregate surface that needs LIKE discrimination: import from the canonical module.

---

## Live frictions / open

### `edge_create` rejects `assertion:N` node endpoints

Logged as friction `assertion:8269` on `service:cortex`. Tracked as `todo:cortex-edge-endpoint-namespaced-id-validation`. Workaround: use reasoning edge to parent entity. See anti-pattern #5 above for full context.

### `impact_warning` on supersedes of short pointer claims

Known, non-blocking. Long-term fix: §3.3 multi-representation enrichment provides the precision layer. When Phase 1 lands fully, this warning class largely disappears.

---

## Sunset condition

This skill retires when **Phase 3 of v2.4 ships** (neighbor expansion via `include_neighbors` with the §6.5 deterministic rule). At that point:

1. Fold "Architectural invariants that bear weight" into the v2.4 spec as a §0 preamble or §9 retrospective.
2. Fold the tier-ladder cheat sheet into §5.5.6 as expanded operational notes.
3. Migrate live frictions to v2.4 closure notes or to standalone agent-bus threads.
4. Delete this file. Update `cortex-orientation.mdc` and the agent-skill registry.

**Update discipline while alive:** on every v2.4 slice ship, bump version, add to "Where we are (shipped)" with date and assertion ID(s), prune from "Where we're going."

---

## Trigger phrases

"v2.4", "v2.4 phase", "intent=", "intent=card", "intent=cluster", "intent=impact", "entity_get card", "Card v0", "predicate_form", "predicate_summary", "compaction pointer read", "compaction pointer filter", "tier ladder", "π reranker", "Stage-2 priority", "neighbor expansion", "cognitive cache", "projection-aware fetch", "multi-representation enrichment", "templated view maintenance", "cognitive maintenance".
