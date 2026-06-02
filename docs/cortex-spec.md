# Cortex Read-Model Spec (v2.4 + v3.0)

**Status:** consolidated public reference
**Scope:** The Cortex storage and read model — entities, assertions, edges, the
confidence/derivation/temporal taxonomy, the `cortex://` URI scheme, and the
BYO-storage model.

This document is the read-model layer. The **write-time discipline** that sits on
top of it — auditor-validatability, cross-model independence, forward-looking
provenance — is specified separately in:

- [`docs/architecture/cortex-provenance-substrate-v1.md`](architecture/cortex-provenance-substrate-v1.md) — universal provenance discipline
- [`docs/architecture/entity-backed-claim-provenance.md`](architecture/entity-backed-claim-provenance.md) — first domain instantiation (authored artifacts)

Where those specs reference `spec:cortex-v2.4` (the read model) or
`document:cortex-v3-spec` (the v3 additions), **this document is that reference.**

The implementation lives in [`libs/cortex_store/`](../libs/cortex_store/);
belief-revision behavior is validated in
[`docs/agm-compliance-report.md`](agm-compliance-report.md) (25/25 AGM postulate
tests).

---

## 1. Design principle

Enforce correct behavior at the API/tool layer so the right path is the path of
least resistance. The schema supports correct provenance; the API makes it the
default rather than an opt-in. Protocol reminders are backup only.

The model draws on temporal knowledge-graph literature (valid-time + text
grounding), the PROV-DM provenance model, AGM belief revision (immutable
assertions + supersession), and graph-native cognitive-memory primitives
(URI addressing, prospective indexing, BYO-storage).

---

## 2. Entity model

An entity is a typed node: a person, organization, decision, todo, document,
service, etc.

```
entities
├── id: TEXT PK            — type:slug  (e.g. person:ada-lovelace)
├── type: TEXT             — namespace: person, organization, decision, todo, ...
├── name: TEXT             — display name
├── description: TEXT      — contrastive, used for entity resolution
├── status: TEXT           — confirmed / provisional / merged / deprecated
├── aliases: TEXT (JSON)   — alternate surface forms
├── attributes: TEXT (JSON)— structured properties
├── notes: TEXT
├── source_uri: TEXT
└── created_at, updated_at: TEXT
```

- **ID format:** `type:slug` (the database primary key).
- **URI format:** `cortex://type/slug[?r=N][&a=artifact]` (see §6).

---

## 3. Assertion model

An assertion is an append-only, versioned claim about an entity. Revision is by
**supersession**, never mutation: the old assertion closes (`superseded_by` set)
and a new one opens. The chain is preserved and queryable.

```
assertions
├── id: INTEGER PK
├── entity_id: TEXT FK        — the subject entity
├── claim: TEXT               — short factual text (always inline)
├── confidence: TEXT          — confirmed / believed / suspected / hypothesized
├── derivation_type: TEXT     — see §4
├── evidence: TEXT            — free-text justification
├── evidence_uris: TEXT(JSON) — source pointers
├── chunk_id: TEXT            — backing source chunk (for quotation/compression)
├── valid_from, valid_until   — world-time bounds (when the claim is true)
├── observed_at, created_at   — system-time bounds (when it was recorded)
├── superseded_by: INTEGER    — FK to the assertion that replaced this one
├── quality_score: REAL       — computed at write time (see §5)
├── resolution_status: TEXT   — pending / fulfilled / breached / unknown / null
├── fulfillment_assertion_id  — FK to the assertion that resolved a commitment
│
│   v3 additions:
├── prospective_summary: TEXT — LLM-generated future-relevance projection
├── events_json: TEXT         — structured [{event, consequences[], timestamp?}]
├── artifact_uri: TEXT        — pointer to a large payload (see §7)
└── artifact_storage: TEXT    — inline / local / rag / arkiv
```

### 3.1 Confidence ladder

`confirmed` > `believed` > `suspected` > `hypothesized`.

`confirmed` is the load-bearing rung: the provenance-substrate spec makes it
*structurally impossible* to mark a claim `confirmed` unless its evidence path
satisfies the auditor-validatability gate.

### 3.2 Temporal semantics (bitemporal)

Two independent time axes:

- **World-time** (`valid_from` / `valid_until`) — when the claim is true in the world.
- **System-time** (`observed_at` / `created_at`) — when it was recorded.

Supersession ≠ validity-end: superseding corrects the *record*; setting
`valid_until` marks that the *world* changed. These are different events.

---

## 4. Derivation taxonomy

Every assertion declares how it was derived. This is the prior-art extension of
TROVE's text-provenance taxonomy with agent-mediated observation types.

| Derivation type | Meaning | Co-requirements |
|---|---|---|
| `quotation` | Verbatim quote from a source | `chunk_id` + non-empty `evidence_uris` |
| `compression` | Sourced summary | `chunk_id` + non-empty `evidence_uris` |
| `inference` | Agent synthesis from prior context | `reasoning_summary` expected |
| `direct_observation` | Deterministic read | — |
| `agent_observation` | Tool output / runtime observation | — |
| `user_statement` | User stated it directly | — |
| `commitment` | A promised/expected future action | `valid_from`; `valid_until` if deadline known |
| `stated` / `other` | Escape hatches | — |

---

## 5. Write-time quality enforcement (v2.4)

The `assert` API validates provenance at write time.

**Hard reject** (specific diagnostic, not a generic error):

- `derivation_type` absent
- `quotation` / `compression` without both `chunk_id` and non-empty `evidence_uris`
- a date pattern in the claim (or linked chunk) but `valid_from` absent
- `observed_at` absent

**Warn + mandatory staging** (accepted, routed to review):

- `reasoning_summary` absent (especially for `inference`)
- `evidence_uris` present but `chunk_id` null (sourced but unchunked)
- `confidence_score` null on extraction-sourced assertions

**Quality score** (computed on ingest, stored on the assertion):

- 40% provenance completeness (`chunk_id` + `evidence_uris` + `derivation_type`)
- 30% temporal completeness (`valid_from` where applicable, `observed_at`)
- 30% reasoning completeness (`reasoning_summary`, `evidence`)
- score < 0.7 → auto-route to staging regardless of other conditions

### 5.1 Commitment tracking

`resolution_status` (`pending` / `fulfilled` / `breached` / `unknown` / null)
plus `fulfillment_assertion_id` let an assertion express a promised action and
its eventual resolution. Open commitments are surfaced at session boot.

```
assertions WHERE resolution_status = 'pending'
  AND (valid_until IS NULL OR valid_until > today)
  ORDER BY valid_from ASC
```

---

## 6. URI scheme (v3)

```
cortex://TYPE/SLUG[?r=REVISION][&a=ARTIFACT]
```

| Component | Maps to | Required | Notes |
|---|---|---|---|
| `TYPE` | `entities.type` | yes | entity-type namespace |
| `SLUG` | slug portion of `entities.id` | yes | with `TYPE`, resolves to `type:slug` |
| `r=N` | the Nth assertion on the entity (creation order) | no | revision pinning; omitted = current |
| `a=ARTIFACT` | `assertions.artifact_uri` | no | dereferences to the payload |

Examples:

```
cortex://decision/rag-noncode-indexing-phased-rollout
cortex://decision/rag-noncode-indexing-phased-rollout?r=3
cortex://person/ada-lovelace
cortex://assertion/847
```

**Backward compatibility:** `type:slug` IDs remain the primary key. `cortex://`
is a resolution layer, not a storage change — all tools continue to accept
`type:slug`. Resolution is a pure API-layer function (`resolve_uri`).

---

## 7. Storage model (BYO-storage, v3)

**Principle:** structured metadata stays in the graph (SQLite); raw artifacts
live elsewhere and are pointed to, never copied in.

| `artifact_storage` | `artifact_uri` format | Use case |
|---|---|---|
| `inline` (default) | NULL | short claims (< 500 chars); claim text is the payload |
| `local` | `files://path/to/artifact` | large text, code, documents in the files sandbox |
| `rag` | `rag://scope/source_hash` | content already indexed in RAG |
| `arkiv` (future) | `arkiv://cid/path` | encrypted decentralized storage |

Migration is additive: existing assertions keep `artifact_storage = 'inline'`
with claim text untouched. No data moves.

---

## 8. Relationship model

Cortex carries **two** kinds of typed, directed link. *Edge* is the genus that
spans them; the two species are the **structural relationship** (this section)
and the **reasoning edge** (§9). They differ along four axes — durability, node
types, attribution, and belief-revision governance:

| Axis | Structural relationship | Reasoning edge (§9) |
|---|---|---|
| Substrate | `relationships` table | `session_edges` table |
| API | `relationship_create` | `edge_create` |
| Nodes | entity → entity | entity or `assertion:{id}` |
| Durability | durable structural fact; consensus-shared | session-attributed; two agents may seed different edges |
| Attribution | not session-scoped | seeded by a session/agent |
| Belief revision | AGM-governed, soft-deleted (§10) | retired (`edge_retire`), not AGM-governed |

A **structural relationship** is a typed, directed edge between two *entities* —
durable, consensus-shared, and the audit ground truth. It records structure that
holds independent of any one reasoning session.

- Relationship types include: `child_of`, `references`, `related_to`,
  `archives_to`, `belongs_to`, `depends_on`, `blocked_by`, `requires`.
- Nodes: entity IDs only (entity → entity).
- Role / strength: a relationship may carry a `role` label and a `strength`
  (0.0–1.0).
- Governance: relationships are soft-deleted (the row is preserved for
  provenance) and participate in belief revision (§10).
- `requires` is the manifest-dependency relation (e.g. a `project` / `plan`
  requires an `agent_skill`); audit detectors treat the structural side as the
  ground truth.

A type name may be registered on **both** substrates intentionally — the same
concept mirrored onto the structural and reasoning layers so each substrate's
traversal can follow it (e.g. `requires`, `depends_on`). The substrate is
determined by which API created the link (`relationship_create` vs
`edge_create`) and which table a query reads, not by the type string alone.

---

## 9. Reasoning edge model

Typed, directed reasoning links connect entities (and assertions) across
sessions, making cognitive/dependency structure queryable. Unlike structural
relationships, reasoning edges are session-attributed: two agents may seed
different edges, and they are retired rather than AGM-superseded.

Edge types include: `reasoned_about`, `caused_by`, `contradicts`, `extends`,
`supersedes`, `analogous_to`, `evidence_for`, `derived_from`, `depends_on`,
`promises`, `expects`, `leads_to`.

- Nodes: entity IDs or `assertion:{id}`.
- Strength: 0.0–1.0 (0.8 default for explicit unweighted edges).
- `derived_from` / `depends_on` enable impact-analysis traversal (downstream
  propagation, blast-radius queries).

### Traversal contract (both substrates)

Because a type may be mirrored onto both substrates (§8), a graph primitive that
reads only one substrate silently under-counts links that live on the other. The
contract is therefore stated **per entry point** — which substrate(s) each
primitive walks:

| Entry point | Op | Substrate(s) walked |
|---|---|---|
| Reverse-dependency BFS | `impact` | **both** — `relationships` ∪ `session_edges` |
| Subgraph render | `render_subgraph` | `relationships` only (structural) |
| Reasoning-edge walk | `edge_traverse` | `session_edges` only (reasoning) |
| Spreading activation | `activate` | `session_edges` only (reasoning) |
| Entity read / card | `entity_get`, `card` | both (surfaced separately) |
| Semantic pre-write impact | `analyze_impact` | neither — FTS5 + vector over claim text, not an edge walk |

**`impact` is the only edge-walk primitive that unions both substrates.** It
answers "if seed *S* changes, what depends on *S*?" as the reverse-dependency
closure: at each frontier node *N* it follows dependency edges whose **target**
is *N* and collects the **source** as a newly impacted dependent (an edge
`X --type--> Y` means *X* depends on *Y*, so a change to *Y* impacts *X*). The
propagating type set is the knowledge-propagation union
`{requires, depends_on, derived_from, evidence_for, extends}`; `blocked_by` is
excluded (workflow/scheduling state, not content/validity dependency). Each
substrate applies its own active predicate: `session_edges` requires
`valid_until IS NULL`; `relationships` requires `active = 1 AND valid_until IS NULL`.

**De-dup rule.** Since the same logical dependency can be mirrored on both
substrates (e.g. `requires`), the union de-dups on the source entity per hop, and
each impacted entity records the substrate(s) it was found on — `structural`
(consensus ground truth) and/or `reasoning` (session-attributed). A dependency
present on both substrates collapses to a single impacted entity whose provenance
carries **both** substrate tags.

**Known gap.** `activate` (spreading activation) reads `session_edges` only and
does not yet union structural `relationships`; structural-only dependencies (e.g.
an audit-enforced `requires`) are invisible to it. Treat its blast radius as
reasoning-substrate-only until that primitive is reconciled.

---

## 10. Belief revision (AGM)

Cortex is AGM-compliant (Alchourrón–Gärdenfors–Makinson). Supersession preserves
both rows; the chain is queryable; lower-entrenchment beliefs contract first;
revisions make minimal changes; the full dependency cascade stays traceable.
Behavioral conformance is validated against the postulate suite — see
[`docs/agm-compliance-report.md`](agm-compliance-report.md) (25/25 tests;
Recovery intentionally rejected).

---

## 11. Forward-looking provenance (v3)

Two assertion fields make the supersession chain a *learning substrate*, not just
an audit trail:

- **`prospective_summary`** — an LLM-generated projection, written at assert time,
  of future scenarios that make the claim relevant (using different vocabulary
  than the claim, to bridge the cue-trigger semantic gap that pure-similarity
  retrieval misses).
- **`events_json`** — structured `[{event, consequences[], timestamp?}]`,
  preserving causal detail that narrative compression would otherwise lose.

Generation is async and non-blocking: if it fails, the assertion still commits;
the projection is backfilled during consolidation. The full contract for these
fields is in the provenance-substrate spec (§4.7).

---

## 12. Version history

| Version | Changes |
|---|---|
| v2.0 | Initial v2: provenance, temporal, staging |
| v2.1 | Tool consolidation (11 tools → `cortex(tool=...)`) |
| v2.2 | Session edges (Phase 1) |
| v2.3 | Session-edge reasoning graph |
| v2.4 | Write-time enforcement, commitment tracking, ingest tooling |
| v3.0 | URI scheme, BYO-storage, prospective indexing, event extraction, 2 new edge types |
