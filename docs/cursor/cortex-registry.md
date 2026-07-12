# Cortex Feature Registry

> Living registry of all Cortex capabilities, classified by provenance.
> Update this file when adding or modifying features in `libs/cortex_store/`.
>
> For the agent-facing capability reference (grouped by purpose, with tool signatures),
> see `notes/system/shared/operational-lessons.md` (MCP cortex sandbox).
>
> Cortex is authoritative for its own foundation. External papers may still be
> useful for comparison, but they are not normative for this registry.
>
## Foundation (Cortex-Native — 11 Primitives)

Every row defines a Cortex foundation primitive and its implementation.
When a primitive affects belief revision semantics, note the relevant AGM postulate.

| ID | Primitive | Cortex Semantics | Implementation | REST Endpoint | MCP Dispatch Op | Status |
|---|---|---|---|---|---|---|
| A1 | Immutable Revisions | Active belief history is append-only; revision closes prior claims via supersession rather than mutation | `assertions` table, `superseded_by` column | `POST /assertions`, `PATCH /assertions/{id}` | `assert`, `assertion_update` | Complete |
| A2 | Mutable Tag Pointers | Named pointers move independently of assertion history to identify canonical or workflow-specific states | `tag_assignments` table (migration 022), `routes/tags.py` | `PUT /tags`, `GET /tags`, `DELETE /tags/{name}` | `tag_assign`, `tag_list`, `tag_resolve` | Complete |
| A3 | Typed Directed Edges | Explicit reasoning links connect entities across sessions and make dependency structure queryable | `session_edges` table (migration 019), `routes/edges.py` | `POST /edges`, `GET /edges`, `GET /edges/traverse` | `edge_create`, `edges`, `edge_traverse`, `edge_retire`, `edge_types` | Complete |
| A4 | URI Addressing (`cortex://`) | Stable addresses resolve entities and optionally pinned assertions | `routes/resolve.py`, `parse_cortex_uri()` | `GET /resolve?uri=cortex://TYPE/SLUG` | `resolve` | Complete |
| A5 | AGM Belief Revision (K*2–K*8) | Revision semantics are validated behaviorally against the AGM postulate suite | `supersede_assertion()` in `routes/assertions.py`, `tests/test_agm_compliance.py` (25 tests) | `POST /assertions/supersede` | `supersede` | Complete (25/25 AGM tests, Recovery postulate intentionally rejected) |
| B1 | FTS5 Fulltext Search | Lexical retrieval covers claims plus enrichment text | `assertions_fts` table (migration 020), `_fts_search()` in `routes/assertions.py` | `GET /assertions/search?q=...` | `search` | Complete |
| B2 | Vector Search + CombMAX Fusion | Hybrid retrieval merges dense and sparse evidence into one result surface | `vector_store.py`, `embeddings.py`, `_combmax_fuse()` in `routes/assertions.py` | `GET /assertions/search?q=...` (hybrid) | `search` | Complete |
| B3 | Client-Side LLM Reranking Metadata | Retrieval outputs expose score components so callers can rerank or inspect evidence provenance | `bm25_score`, `cosine_similarity`, `combmax_score`, `retrieval_source` in search results | `GET /assertions/search` response fields | `search` results | Complete |
| C1 | Impact Analysis (BFS) | Revision blast radius is queryable across dependency edges spanning **both** substrates (structural `relationships` ∪ reasoning `session_edges`); each impacted entity carries substrate provenance | `graph_utils.py` `analyze_impact()` (shared `edge_walk.active_edges`), `routes/graph.py` | `GET /edges/impact?entity_id=...` | `impact` | Complete |
| C2 | Write-Path Contradiction Detection | Cross-entity contradictions are flagged at write time rather than only during later review | `graph_utils.py` `check_contradictions()`, auto-runs at `POST /assertions` | Automatic at assertion create | Automatic | Complete |
| D1 | Safety-Hardened Consolidation (Dream State) | Automated contraction is guarded by staged application, circuit breakers, and dry-run defaults | `pipelines/dream_state/` (7-step pipeline), `GuardedApplyHandler`, 50% circuit breaker, dry-run default | Pipeline execution | `pipeline(pipeline="dream-state", ...)` | Complete |

## Extensions (Lived — 16 capabilities)

Capabilities beyond the foundational belief revision primitives. Classified by origin and purpose.

### Pre-v3 (independent Cortex work, preserved through v3)

| ID | Capability | Classification | Implementation | REST Endpoint | MCP Dispatch Op |
|---|---|---|---|---|---|
| X1 | Multi-Agent Coordination | coordination | 4+ agent personas with distinct boot profiles, `agent`+`session_id` on edges | Boot profiles in `cortex_named_tools.py` | `cortex_brief(agent=...)` |
| X2 | Salience-Driven Boot | retrieval | `salience.py`, `scoring.py`, `routes/boot.py`, EST dual-track gating | `GET /boot-sections`, `GET /boot-temporal`, `GET /boot-todos` | `cortex_brief` |
| X3 | Confidence Derivation Types | operational | `derivation_type` column: direct, agent_observation, inference, compression, quotation, stated, commitment | On assertions | `assert` field |
| X4 | Write-Time Quality Validation | operational | `assertion_quality.py`, hard rejects (422), soft warnings (staging), quality scores | Automatic at `POST /assertions` | Automatic |
| X5 | Bitemporal Bounds | operational | `valid_from`/`valid_until` (world-time), `observed_at`/`created_at` (system-time), `GET /boot-temporal` | `GET /boot-temporal`, `GET /assertions?valid_at=...&known_at=...` | Assertion filters |
| X6 | Journal Protocol & Episodic Memory | coordination | `session_journals` table, mandatory session journaling, RAG-indexed | `GET /session-journals`, `POST /session-journals` | `journal_read`, `journal_write` |
| X7 | Friction/Observe Inline Logging | operational | Lightweight assertion creation with sensible defaults, `derivation_type=agent_observation` | `POST /assertions` (via observe/friction) | `observe`, `friction` |
| X8 | Review Queue / Staging Pipeline | operational | `review_status` lifecycle: committed → flagged → staged → rejected | `GET /assertions?review_status=...`, `GET /staging` | `review_queue`, `cortex_staging_list`, `cortex_staging_reject` |
| X9 | Near-Duplicate Detection | operational | `near_dup.py`, semantic similarity check at write time, `near_duplicate_warning` in response | Automatic at `POST /assertions` | Automatic |
| X10 | Document Ingestion + Chunks | retired | RAG is now authoritative; cortex chunks/ingest routes removed (master @ 25a2260a). Provenance via `chunk_id` (RAG-deterministic, `{content_hash_prefix}-{i}`) + `evidence_uris[0]` on assertions; resolve via `libs/cortex_store/rag_resolver.py`. | RAG: `POST /api/v1/rag/chunks_by_index`; cortex: `GET /assertions/{id}/chunk` resolver | `cortex_resolve_assertion_chunk` |

### Added in v3 Implementation (Phases A–D)

| ID | Capability | Classification | Implementation | REST Endpoint | MCP Dispatch Op |
|---|---|---|---|---|---|
| X11 | Entrenchment Ordering | retrieval | `entrenchment.py` `compute_entrenchment()`, concrete implementation of AGM K*7/K*8 contraction ordering | `GET /assertions/entrenchment?entity_id=...` | Embedded in search/boot results |
| X12 | Spreading Activation (C3) | retrieval | `activation.py` `spreading_activation()`, BFS with decay + hub suppression over **both** substrates (structural `relationships` ∪ reasoning `session_edges`) via shared `edge_walk.active_edges`; hub degree + denominator span both substrates; `substrates_traversed` provenance on results | `GET /assertions/activate?entity_ids=...` | `activate` |
| X13 | Assertion Embeddings | retrieval | `embeddings.py`, `vector_store.py` (ChromaDB), background embedding on write | Automatic at write + `GET /assertions/search` | Via `search` (hybrid mode) |

### Post-v3 (Boot Redesign, 2026-04-07)

| ID | Capability | Classification | Implementation | REST Endpoint | MCP Dispatch Op |
|---|---|---|---|---|---|
| X14 | Boot Activation Pass | retrieval | `_boot_activation_pass()` in `cortex_named_tools.py` — spreading activation + hybrid search from continuation context at boot time | Via `cortex_brief` response (`activated_context`) | `cortex_brief` |
| X15 | Domain Depth Detection | coordination | `_detect_boot_domains()` in `cortex_named_tools.py` — keyword detection of employment/legal/financial domains from continuation state | Via `cortex_brief` response (`domain_depth_hints`) | `cortex_brief` |
| X16 | Notes-to-Self Protocol | operational | Session close protocol in `_operational_context.py` — agents seed 2-5 effectiveness observations before journaling | Via `observe` | `observe` |

### Post-v3 (Ephemeral Entities, 2026-04-07)

| ID | Capability | Classification | Implementation | REST Endpoint | MCP Dispatch Op |
|---|---|---|---|---|---|
| X17 | Retention Policies + Access TTL | operational | `retention_policy`, `retention_ttl_days`, `last_accessed_at` on entities (migration 026). Trigger materializes last_accessed_at from entity_access_log. Batch access logging in search, activate (seeds), tag_resolve. | Entity create/update fields | `entity_create`, `entity_update` fields |
| X18 | Transcript Entities + Session Chaining | coordination | `transcript` entity type with ephemeral retention. `continues` edge type for temporal session chains. `relates_to` edge type registered. Cross-session continuity via graph edges. | Entity CRUD + edge CRUD | `entity_create`, `edge_create` |
| X19 | Reaper Process | infrastructure | `routes/reaper.py` — sweeps ephemeral entities past TTL with low entrenchment. 1-hop permanent-entity edge protection. Soft-delete only. Manual trigger initially. | `POST /reaper/run`, `GET /reaper/preview` | Via dispatch |

## Classification Guide

### Step 1: Classify (during spec or review)

Before writing code, answer:

- **Does it implement or extend a Cortex foundation primitive?** → Foundation. Name the primitive and cite the AGM postulate only when revision semantics are affected.
- **Is it a new capability?** → Extension. Assign a classification type:
  - `retrieval` — affects how information is found (search, activation, ranking, boot sections)
  - `operational` — affects how information is managed (quality gates, enrichment, dedup, staging, retention)
  - `coordination` — affects multi-agent behavior (boot scoping, journaling, domain detection, transcripts)
  - `infrastructure` — affects system plumbing (storage, embeddings, migrations, reaper)
- **State the classification in the spec or agent-bus review reply.**

### Step 2: Implement

- Add the registry row(s) to this file in the same commit as the feature.
- Use the next available ID: Foundation rows use paper primitive IDs (A1–D1). Extensions use X{N+1}.

### Step 3: Update agent-facing docs

- If the feature adds or changes MCP tool surface: update `notes/system/shared/operational-lessons.md` (MCP cortex sandbox).
- If the feature changes boot behavior: check `notes/system/boot-redesign-kumiho.md` (MCP cortex sandbox, historical filename).
- If the feature adds a new entity type or assertion behavior: check `notes/system/shared/rules/cortex-orientation.md`.

### Reference

- AGM compliance report: `docs/agm-compliance-report.md` (25/25 postulate tests)
- Implementation proof: `notes/system/kumiho-implementation-proof.md` (MCP cortex sandbox, historical comparison only)
- Boot redesign spec: `notes/system/boot-redesign-kumiho.md` (MCP cortex sandbox, historical filename)

## Provenance Summary

| Category | Count | Origin |
|---|---|---|
| Foundation | 11 | Cortex foundation primitives |
| Pre-v3 Extensions | 10 | Independent convergence, preserved through v3 |
| Phase A–D Extensions | 3 | Added during v3 implementation sprint |
| Post-v3 Extensions (Boot) | 3 | Boot redesign |
| Post-v3 Extensions (Ephemeral) | 3 | Ephemeral entities (decision:ephemeral-entity-lifecycle) |
| **Total** | **30** | |
