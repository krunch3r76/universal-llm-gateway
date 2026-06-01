# AGM Belief Revision Compliance Report

**System:** Universal LLM Gateway — Cortex Cognitive Memory Layer  
**Reference Frame:** AGM belief revision postulates (Alchourron, Gardenfors, Makinson, 1985)  
**Test Suite:** `libs/cortex_store/tests/test_agm_compliance.py`  
**Last Updated:** 2026-04-06  
**Status:** 25/25 tests passing (2.16s)

---

## Background

Cortex is the persistent cognitive memory layer within the Universal LLM Gateway (ULG). It provides typed entities, append-only assertions with supersession chains, session-scoped reasoning edges, and named tag pointers — enabling frontier LLM agents to maintain coherent, revisable beliefs across sessions.

Cortex defines its own belief-revision foundation. This compliance suite tests that
foundation directly against the AGM postulates of rational belief revision
(Alchourron, Gardenfors & Makinson, 1985) through Cortex's live API surface.
Related papers may still be useful as historical comparison, but they are not
normative for Cortex.

## AGM Postulate Coverage

### K∗2 — Success

> After revision with new evidence, the new belief exists in the belief base.

| Test | Description | Status |
|------|-------------|--------|
| `test_supersede_creates_new_active_belief` | Superseding an assertion creates a new active belief with a distinct ID | ✅ |
| `test_superseded_belief_is_inactive` | The original assertion is no longer in the active belief set after supersession | ✅ |

**Mechanism:** `POST /assertions/supersede` atomically creates the new assertion and sets `superseded_by` on the old one. The new assertion is immediately queryable; the old one is excluded from active queries.

### K∗3 — Inclusion

> Expansion preserves existing beliefs that don't contradict.

| Test | Description | Status |
|------|-------------|--------|
| `test_new_belief_coexists_with_existing` | Adding a non-contradictory assertion preserves the prior assertion | ✅ |
| `test_multiple_non_contradictory_expansions` | Three sequential compatible assertions all remain active | ✅ |

**Mechanism:** `POST /assertions` is purely additive. New assertions never implicitly modify or remove existing ones — contradiction resolution requires explicit `supersede` calls.

### K∗4 — Preservation

> If the new belief doesn't contradict, all old beliefs survive.

| Test | Description | Status |
|------|-------------|--------|
| `test_adding_compatible_belief_preserves_all` | Four assertions (A, B, C, D) all remain active when none contradict | ✅ |

**Mechanism:** Same as K∗3. Append-only assertion storage guarantees preservation — there is no implicit contraction path.

### K∗5 — Consistency

> The result of revision is consistent (no active contradictions in the resolved belief state).

| Test | Description | Status |
|------|-------------|--------|
| `test_supersede_removes_old_from_active` | After superseding "YAML" with "TOML", only "TOML" is active | ✅ |
| `test_superseded_assertion_has_superseded_by` | The old assertion's `superseded_by` field points to the new assertion's ID | ✅ |
| `test_boot_excludes_superseded` | Active-only queries exclude superseded assertions from the belief state | ✅ |

**Mechanism:** The `superseded_by` column creates a directed supersession chain. All query paths (boot, search, resolve) filter on `superseded_by IS NULL` by default, ensuring the visible belief state contains no contradictions.

### K∗6 — Extensionality

> Equivalent inputs produce equivalent revision behavior.

| Test | Description | Status |
|------|-------------|--------|
| `test_identical_revision_on_two_entities` | Identical revision sequences on two separate entities produce identical belief states | ✅ |

**Mechanism:** Revision is deterministic — the same claim, confidence, and derivation type produce the same structural outcome regardless of which entity they target.

### K∗7 — Superexpansion (Entrenchment-Dependent)

> When revision forces contraction, lower-entrenchment beliefs are contracted first.

| Test | Description | Status |
|------|-------------|--------|
| `test_entrenchment_ordering_by_confidence` | `confirmed/direct_observation` produces higher entrenchment than `hypothesized/agent_observation` | ✅ |
| `test_entrenchment_ordering_via_endpoint` | `GET /assertions/entrenchment` returns assertions in descending entrenchment order | ✅ |
| `test_manual_supersede_respects_entrenchment` | Manual supersession targets the lower-entrenchment belief, preserving the higher one | ✅ |

**Mechanism:** Entrenchment score = `salience_weight × confidence_rank × derivation_weight`.

- **Salience:** `recency × 0.6 + access_freq × 0.4` (exponential decay β=0.001/hr, ~29-day half-life)
- **Confidence rank:** confirmed=1.0, believed=0.75, suspected=0.5, hypothesized=0.25
- **Derivation weight:** direct_observation=1.0, quotation=0.95, agent_observation=0.9, stated=0.85, inference=0.8, compression=0.7

**Caveat:** These tests verify entrenchment ordering and manual supersession. Automated contraction — where the system autonomously decides which beliefs to contract based on entrenchment — is deferred to Phase D1 (Dream State consolidation pipeline). The ordering mechanism is proven; policy enforcement is future work.

### K∗8 — Subexpansion

> Revision makes minimal changes — only the targeted belief is affected.

| Test | Description | Status |
|------|-------------|--------|
| `test_supersede_only_affects_targeted_belief` | Superseding B leaves A and C completely unchanged | ✅ |
| `test_supersede_preserves_metadata_of_siblings` | Sibling assertion's confidence, derivation_type, and superseded_by are untouched | ✅ |

**Mechanism:** `supersede` operates on exactly one assertion ID. No cascade, no side effects. The supersession chain is a linked list, not a tree.

### Relevance

> Contraction affects only beliefs relevant to the contracted belief.

| Test | Description | Status |
|------|-------------|--------|
| `test_supersede_on_entity1_does_not_affect_entity2` | Superseding on entity 1 leaves entity 2's assertions completely unchanged | ✅ |
| `test_cross_entity_isolation_with_multiple_assertions` | Heavy revision on entity 1 (two sequential supersessions) leaves entity 2's three assertions intact | ✅ |

**Mechanism:** Entity-scoped assertion storage provides natural relevance boundaries. Assertions belong to exactly one entity — revision operations cannot cross entity boundaries.

### Core-Retainment

> Core beliefs (high entrenchment, committed status) survive contraction.

| Test | Description | Status |
|------|-------------|--------|
| `test_committed_high_entrenchment_is_undeprecatable` | Committed + confirmed + direct_observation assertions have highest entrenchment and appear first in ordering | ✅ |
| `test_staged_low_entrenchment_is_depreciable` | Staged + hypothesized assertions have low entrenchment and are valid contraction targets | ✅ |

**Mechanism:** The `review_status` field (committed/staged) combined with entrenchment scoring creates a two-tier protection model. Committed high-entrenchment assertions are protected from automated deprecation by the Dream State consolidation policy (Phase D1).

### Tag Pointer Consistency

> Tag reassignment produces correct belief state resolution.

| Test | Description | Status |
|------|-------------|--------|
| `test_tag_resolves_to_assigned_assertion` | Tag "current" resolves to the assigned assertion via `GET /resolve` | ✅ |
| `test_tag_move_updates_resolution` | Moving a tag from A2 to A3 updates resolution to A3 | ✅ |
| `test_resolve_without_tag_returns_latest` | Resolving without a tag parameter returns the entity (not a specific assertion) | ✅ |
| `test_tag_list_shows_all_tags` | `GET /tags` returns all assigned tags for an entity | ✅ |

**Mechanism:** Tag pointers (`tag_assignments` table) provide mutable references to specific assertions within an entity's belief history. Tags are UPSERT-based — reassigning a tag atomically moves the pointer. URI resolution (`GET /resolve`) supports optional `?tag=` parameter for tag-aware retrieval.

### Entrenchment Computation

| Test | Description | Status |
|------|-------------|--------|
| `test_confirmed_direct_highest` | confirmed + direct_observation yields entrenchment > 0.3 | ✅ |
| `test_hypothesized_agent_obs_lowest` | hypothesized + agent_observation yields entrenchment < 0.2 | ✅ |
| `test_supersede_assigns_entrenchment` | New assertions created via supersede receive computed entrenchment scores | ✅ |

---

## Cortex Foundation Primitives

| # | Cortex Primitive | Implementation | Status |
|---|------------------|----------------|--------|
| 1 | Immutable Revisions | Append-only assertions + supersession chains | ✅ Complete |
| 2 | Typed Directed Edges | Session edges with provenance + relationship table | ✅ Complete |
| 3 | Mutable Tag Pointers | `tag_assignments` table + resolve endpoint | ✅ Complete |
| 4 | URI Addressing | `cortex://` scheme + `GET /resolve` | ✅ Complete |
| 5 | FTS5 Text Search | `assertions_fts` with sanitized queries | ✅ Complete |
| 6 | Vector Embeddings | ChromaDB collection + auto-embed on write/supersede | ✅ Complete |
| 7 | Hybrid Search (CombMAX) | Score fusion of BM25 + cosine similarity | ✅ Complete |
| 8 | Prospective Indexing | `prospective_summary` + `events_json` fields | ✅ Complete |
| 9 | Event Extraction | Enrichment pipeline extracts structured events | ✅ Complete |
| 10 | Entrenchment Ordering | Composite score (salience × confidence × derivation) | ✅ Complete |
| 11 | Dream State Consolidation | Automated contraction via entrenchment policy | 📋 Phase D |

---

## Test Infrastructure

- **Framework:** pytest with FastAPI TestClient
- **Isolation:** Fresh in-memory SQLite database per test (full schema bootstrap)
- **No external dependencies:** Tests do not require running services — all operations execute against the TestClient
- **Derivation type:** Tests use `agent_observation` to bypass chunk_id/evidence_uris quality validation — appropriate since AGM tests verify belief revision mechanics, not ingestion provenance

## How to Run

```bash
cd libs/cortex_store
pytest tests/test_agm_compliance.py -v
```

## References

- Alchourron, C. E., Gardenfors, P., & Makinson, D. (1985). On the logic of theory change: Partial meet contraction and revision functions. *Journal of Symbolic Logic*, 50(2), 510–530.
