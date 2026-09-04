# Cortex Read-Model Spec (v2.5 + v3.0)

**Status:** consolidated public reference (`spec:cortex-v2.5`)
**Scope:** The Cortex storage and read model — entities, assertions, edges, the
confidence/derivation/temporal taxonomy, the status-trait normalization read
model (Phase 0), the `cortex://` URI scheme, and the BYO-storage model.

This document is the read-model layer. The **write-time discipline** that sits on
top of it — auditor-validatability, cross-model independence, forward-looking
provenance — is specified separately in:

- [`docs/architecture/design/cortex-provenance-substrate-v1.md`](architecture/design/cortex-provenance-substrate-v1.md) — universal provenance discipline
- [`docs/architecture/design/entity-backed-claim-provenance.md`](architecture/design/entity-backed-claim-provenance.md) — first domain instantiation (authored artifacts)

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
├── status: TEXT           — LEGACY overloaded axis; read-authoritative (Phase 0). See §2.1.
├── aliases: TEXT (JSON)   — alternate surface forms
├── attributes: TEXT (JSON)— structured properties
├── notes: TEXT
├── source_uri: TEXT
│
│   v2.5 status-trait normalization (migration 050, Phase 0 — shadow):
├── lifecycle: TEXT        — hand-set life state: active / superseded / merged / invalidated / dismissed
├── confidence_band: TEXT  — derived band: unsubstantiated / provisional / confirmed (no setter)
├── confidence_score: REAL — derived graded score (propagation Φ*, see §2.2)
├── adoption: TEXT         — decision-type-only: proposed / adopted / superseded
└── created_at, updated_at: TEXT
```

- **ID format:** `type:slug` (the database primary key). For referent types in
  `MINTED_TYPES` (`person`, `organization`), the slug is a server-minted opaque
  lowercase ULID; callers omit `id` at create and supply the display string in
  `name`. All other types keep caller-designated slugs.
- **URI format:** `cortex://type/slug[?r=N][&a=artifact]` (see §6).

### 2.1 Status-trait normalization (v2.5, Phase 0)

The legacy `status` column multiplexed **three orthogonal axes** —
derived confidence, hand-set lifecycle, and (for `decision`) adoption — which is
the root cause of the stuck-label class, the "freeze half a field" contortion,
and the per-type `decision` carve-out. Migration 050 normalizes those axes into
dedicated nullable trait columns. The trait model and its research basis (5GNF
trait externalization, belief-graph Φ/Ψ separation, BiTRDF bitemporal) are
specified in `cortex:notes/system/specs/cortex-status-trait-normalization-spec-2026-06-02.md`.

| Trait | Column(s) | Nature | Setter |
|---|---|---|---|
| **confidence** | `confidence_band`, `confidence_score` | derived, read-only by construction (propagation from backing assertions) | none — derived only (§2.2) |
| **lifecycle** | `lifecycle` | hand-set judgment call (life state) | caller-owned |
| **adoption** | `adoption` | `decision`-type only (was the status-word hijack) | caller-owned |

**Read authority is `status`, not the traits (Phase 0).** The trait columns are
nullable and shadow-populated by the derivation batch (migration 050 is strictly
additive; no read site references them). `status` remains authoritative for every
read site — boot card, audit detectors, `status_summary`. The read cutover to the
trait columns is a **separate, operator-gated step** (Phase 2;
`todo:cortex-status-traits-phase2-cutover`); the eventual drop of `status` is
Phase 3. This spec does **not** claim `status` is retired.

### 2.2 Derived confidence (`confidence_band` / `confidence_score`)

`confidence_score` is a graded propagation value Φ* in [0,1]; `confidence_band`
is its label band (`unsubstantiated` / `provisional` / `confirmed`) under a
promotion threshold τ plus a confirmed-evidence gate. Both are **derived only —
there is no setter** (nothing to "freeze"): they are computed from the entity's
backing assertions, their `credibility` (§3), and signed reasoning edges (§9).
The full derivation contract — Φ/Ψ separation, cluster-max prior, the signed
propagation operator, the confirmed-evidence gate, and the band thresholds — is
specified in the confidence-derivation policy
(`cortex:notes/system/specs/cortex-confidence-derivation-policy-v2.md`,
`derivation_policy_version = confidence-derivation/v2`). Auditor-validatability
is preserved: a `confirmed` band still requires a confirmed, source-citing
assertion, never propagated score alone.

### 2.3 Per-type confidence-field registry (`type_confidence_fields`, migration 047)

Not every entity type carries its auditable confidence on `status`. Migration 047
adds a `type_confidence_fields` registry — a single source of truth declaring
**which field carries each type's auditable confidence axis** — consumed by the
auditor-validatability detectors so Gate-0 membership is a data consequence of
the declaration, not a hand-maintained scope list in detector code.

```
type_confidence_fields
├── entity_type: TEXT PK     — the entity type
└── confidence_field: TEXT   — status | workflow_state | content_hash | none
```

Unregistered types default to `status` (normal Gate-1..4 gating), preserving
historical detector behavior. Seeded non-default declarations:

| Entity type | `confidence_field` | Meaning |
|---|---|---|
| `test` | `none` | `status` is not a confidence axis (bulk fixtures) |
| `todo` | `workflow_state` | confidence rides the workflow lifecycle column |
| `transcript` | `content_hash` | a structural verifier binds entity ↔ artifact |

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
├── credibility: TEXT         — external per-source/per-assertion trust Ψ (v2.5, migration 050); see §3.3
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

Views inherit both axes at document scale — see §12.5.

### 3.3 Credibility Ψ (v2.5)

`credibility` is **external** trust — how much we trust *where a claim came
from* (source provenance / annotation quality), kept deliberately separate from
the internal, derived entity `confidence` (§2.2). It lives on the assertion, is
a-priori, and feeds the derivation as the Ψ term (`b = λΨ + (1−λ)c`). The column
is nullable (migration 050); NULL resolves to the `unrated` floor at derivation
time. The credibility ladder and its host/derivation-type resolution are part of
the derivation policy (§2.2): operator `user_statement` / `direct_observation`
self-source as `internal-authority`; `agent_observation` as `external-KB`;
external citation hosts resolve via a computed `*.gov`→authority rule plus a
small hand-maintained host list.

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

**Predicate-normalize graduation (third outcome)** — distinct from staging and
hard reject: when write-time ``normalize_predicate_domain`` fires Class 6,
invention, or re-subjection guards, the assertion is **accepted** with
``review_status='flagged'`` and distinguishable ``review_notes`` reason tokens
(``class6_generic_state`` / ``invention`` / ``resubjection``). Sync explicit-
``predicate_form`` writers receive ``flag_reasons``, ``normalization_decision``,
``suppression_effect``, and ``_next`` in the ``predicate_form_normalize`` ACK
envelope; async extract writeback carries the same fields on
``mcp.cortex.predicate.review.required`` (session_id carrier). T0 mechanical
re-normalize may clear flags without rewriting ``predicate_form`` when guards
no longer fire after a ``normalizer_version`` bump.

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
| `SLUG` | slug portion of `entities.id` | yes | with `TYPE`, resolves to `type:slug`; opaque ULID for minted referent types |
| `r=N` | the Nth assertion on the entity (creation order) | no | revision pinning; omitted = current |
| `a=ARTIFACT` | `assertions.artifact_uri` | no | dereferences to the payload |

Examples:

```
cortex://decision/rag-noncode-indexing-phased-rollout
cortex://decision/rag-noncode-indexing-phased-rollout?r=3
cortex://person/ada-lovelace
cortex://person/01k3mz8f4v9q2x7c5b6n8t1r3e
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

Structural `derived_from` registration for derived views is specified in §12.1.

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
| Subgraph walk | `walk_subgraph` | `relationships` only (structural) — lean topology, no assertion canvas; depth-aware neighbor fidelity on `render_subgraph` mirrors assertion **8164** (see `entity_get` / boot sparse rendering) |
| Reasoning-edge walk | `edge_traverse` | `session_edges` only (reasoning) |
| Write-path contradiction | `check_contradictions` | `session_edges` only (reasoning — by design; see below) |
| Spreading activation | `activate` | **both** — `relationships` ∪ `session_edges` |
| Entity read / card | `entity_get`, `card` | both (surfaced separately) |
| Semantic pre-write impact | `analyze_impact` | neither — FTS5 + vector over claim text, not an edge walk |

**Two edge-walk primitives union both substrates** — `impact` and `activate` —
through the shared read-layer primitive `edge_walk.active_edges(node, *, types,
direction)`, so the two substrates are reconciled **once** rather than per-call.
Each substrate applies its own active predicate: `session_edges` requires
`valid_until IS NULL`; `relationships` requires `active = 1 AND valid_until IS NULL`.

`impact` answers "if seed *S* changes, what depends on *S*?" as the
reverse-dependency closure (`direction="reverse"`): at each frontier node *N* it
follows dependency edges whose **target** is *N* and collects the **source** as a
newly impacted dependent (an edge `X --type--> Y` means *X* depends on *Y*, so a
change to *Y* impacts *X*). Its propagating type set is the knowledge-propagation
union `{requires, depends_on, derived_from, evidence_for, extends}`.

`activate` answers "what is associatively related to seed *S*?" as an undirected
spread (`direction="both"`): at each frontier node it collects the opposite
endpoint of every incident edge. Because association is broader than dependency,
it walks the full knowledge-association set `{relates_to, related_to, references,
child_of, belongs_to, archives_to, depends_on, requires, derived_from,
evidence_for, extends, supersedes, caused_by, analogous_to, contradicts}`, and its
hub-suppression denominator and per-entity degree both count both substrates so
the IDF penalty stays coherent with the unioned walk. Each activated assertion
carries `substrates_traversed`.

`blocked_by` is excluded from **both** type sets — it is workflow/scheduling state
("A waits on B"), not content/validity dependency or knowledge association. A
workflow view ("what is waiting on this?") is an explicit opt-in, never a default
traversal.

**De-dup rule.** Since the same logical link can be mirrored on both substrates
(e.g. `requires`, `contradicts`), the union de-dups neighbors per hop — the visited
set guards node re-visits, and `active_edges` emits one row per substrate so a
mirrored edge legitimately contributes from each. `impact` records the substrate(s)
each impacted entity was found on — `structural` (consensus ground truth) and/or
`reasoning` (session-attributed); a dependency present on both collapses to a
single impacted entity whose provenance carries **both** substrate tags.

**Provenance field semantics.** `impact.substrates` is **aggregated provenance** —
for each impacted entity, `_path_edges` recomputes all edge rows along the path
and unions both substrates, so a dual-mirrored edge contributes both tags.
`activate.substrates_traversed` is a **per-hop path trace** — each entry records
the substrate of the edge actually traversed; the BFS visited-set short-circuits
the second substrate row for a mirrored neighbor, so first-seen is the correct
trace.  The two fields answer different questions and their asymmetry is
intentional, not a coverage gap.

**Per-primitive substrate contract.** `check_contradictions` — the write-path
belief-contradiction check in `graph_utils` at `POST /assertions` — reads
`session_edges` only and walks the `contradicts` type **by design**, not by
omission.  The structural and reasoning `contradicts` are homonymous but
semantically distinct: migration 007 registers structural `contradicts` as one of
the eight event-to-event relation types in the event-chain infrastructure (sibling
to `precedes`, `causes`, `enables`, `elaborates`, `co_occurs`, `supersedes`,
`responds_to`), expressing tension between *events*, not between beliefs or
assertions.  The reasoning `contradicts` on `session_edges` is the AGM-adjacent
belief-tension type that `check_contradictions` is designed to detect.  Unioning
both substrates would conflate these concepts, causing the belief-contradiction
check to consume event-chain tension as if it were assertion conflict — a semantic
error, not a coverage fix.  This substrate boundary is documented as a
per-primitive contract (cortex assertion 11854).  (`render_subgraph` is
structural-only and `edge_traverse` is reasoning-only — both are entry-point
contracts by the same reasoning.)

---

## 10. Belief revision (AGM)

Cortex is AGM-compliant (Alchourrón–Gärdenfors–Makinson). Supersession preserves
both rows; the chain is queryable; lower-entrenchment beliefs contract first;
revisions make minimal changes; the full dependency cascade stays traceable.
Behavioral conformance is validated against the postulate suite — see
[`docs/agm-compliance-report.md`](agm-compliance-report.md) (25/25 tests;
Recovery intentionally rejected).

Document-scale refresh conformance rides a KM-grounded postulate suite — §12.8.

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

Document-scale derivation provenance is specified in §12.4.

---

## 12. Derived views (v3.1)

### 12.0 Card current-status pin (qualified projection)

Card v0 exposes ``current_status`` as a **qualified projection** on the read
path (not a silent scalar promote):

```json
{
  "served": { "...": "best-supported unflagged status(%, current) row" },
  "review_status": "committed",
  "observed_at": "...",
  "source": "<assertion_id>",
  "withheld_newer": [
    {"assertion_id": 29834, "state": "...", "reason": "class6_generic_state", "observed_at": "..."}
  ],
  "withheld_count": 1
}
```

- **Source** — ``current_status.source`` names the assertion id (or derivation
  label) backing ``served``.
- **Freshness** — ``current_status.observed_at`` names when the served row was
  observed.
- **Recovery** — ``withheld_newer`` lists newer flagged status candidates (cap
  3; ``withheld_count`` is the total beyond the cap). Sync writers see ``_next``
  in the ``predicate_form_normalize`` ACK; async extract writers see the same on
  ``mcp.cortex.predicate.review.required``.
- **Blank pin** — entities with no status rows render ``served: null``,
  ``withheld_newer: []``, ``withheld_count: 0`` (explicit absence).
- **top_k disclosure** — flagged assertions remain in ``top_k_assertions`` with
  inline ``epistemic_state: "flagged"`` (annotate, do not filter).

A *view* is a registered `document:` entity that is a derived projection of graph
state — deterministic **core** (linear verbalization: canonically serialized,
hashed) + cited **narrative** (model-authored; every non-obvious claim cites
`[assertion:N]` or a source URI). A registered view is invalid without a derivation
recipe + refresh contract (P1). Views are STORED/materialized with staleness
detection; salience remains computed (G8; consistent with DQ8). Materialization is
an economics decision — the matter stage gate (S0/S1/S2, see
`matter-playbook-lifecycle`) *is* view-selection policy, formally (P1). The
cortex-spec owns the artifact class, storage grain, op contract, detector taxonomy,
stamp/PROV shapes, refresh postulates, and profile registry-as-data;
`matter-playbook-lifecycle` keeps lifecycle *procedure* (when to render, stage
gates, insight routing, ring/RESUME).

### 12.1 Registration & storage grain

`derived_from` structural relationship (document → root; registered type; mirrors
the §9 reasoning-edge type per §8's dual-substrate note) + document attributes
`view_profile`, `derivation_recipe`, `derived_from_snapshot {max_assertion_id,
as_of}`, `view_rev`, `core_hash`, `section_stamps`, `prior_revision_uri`. Index
profiles are selector-scoped and may omit the root edge. Note: `derived_from`
participates in `impact`'s propagating union (§9) — changing a root surfaces its
views as impacted.

### 12.2 Graph-canonical vs view-authored facts

**Graph-canonical** (must resolve to assertions/edges; views render with
citation): deadlines/dates · amounts/figures · counterparty names/roles/contacts ·
case/work-order/reference numbers · dispositions + closing assertions · commitments
+ `resolution_status` · entity/thread/document identifiers · mode/stage.

**View-authored** (narrative): synthesis, emphasis, ordering rationale, strategy
framing, lesson narratives — cited per the grammar.

**Core fields** (deterministic, hashed): handle id · mode · stage · ring id · case
ids · view doc ids · `imprint_rev` · `derived_from_snapshot` · open-front rows
{item, next move, owner, deadline, source assertion ids} · deadline rows · cast
rows {entity id, role} · ruled-out rows {item, closing assertion id} ·
open-commitment rows {assertion id, due}.

### 12.3 View profiles

Each derivation row doubles as the section's **watched set** (Tier-0 localization
+ anti-amnesia boundary); the render op records watched sets in the derivation
stamp.

**Charter** (`view_profile=matter_charter`):

| Section | Derivation source (= watched set) |
|---|---|
| Header block (core) | handle card: id, genus, mode, stage, ring, cases, `imprint_rev`, snapshot |
| ▶ FRONT LINE | open-front assertions on handle/cases + deadline entities + open commitments (`resolution_status=pending`) |
| TIMELINE SPINE | dated assertions (`valid_from`) + event chains on cases, ordered |
| ✗ RULED OUT / disagreements | superseded chains + closing assertions + `contradicts` (reasoning) |
| CAST & ROUTING | `involves`/`correspondence_with`/`employed_by` edges → person:/organization: cards + role labels (walk_subgraph 1-hop) |
| ⚠ LESSONS & FRICTIONS | live lesson assertions on the handle |
| OPEN THREADS | pending commitments + unanswered counterparty items (`resolution_status=pending`) |
| DOCTRINE pointer | live doctrine = hop-0 handle/case assertions; archived bodies via `search` / orientation-pointer clause (no typed playbook edge — `has_playbook` retired, arc 4917) |
| ENTITY MAP | walk_subgraph(handle, 1–2 hops) — every row a live id |
| Narrative layer | authored synthesis over the above; every claim cites; persona-free |

**Doctrine** (`view_profile=matter_doctrine`): halt-canary table · disambiguation
tables · decision structures (owner/threshold/fallback) · counterparty-position map ·
channel discipline · verification gate · documents-in-evidence keystones — all
matter-specific rows graph-cited; stale rows are Tier-0 (v1) / Tier-1 (v2)
findings.

**Index** (`view_profile=matter_index`): deterministic row set over a selector
(e.g. active cases/handles): {entity id, name, stage/status, next deadline, view
doc id} — core-only profile (no narrative layer).

### 12.4 Derivation provenance

Per-instance qualified stamp
`{used: [derived_from_snapshot, watched-set refs, prior_revision_uri], activity:
{op: view_render, recipe: recipe:{profile}/v{n}, mode, agent, session_id},
generated: {document_id, view_rev, core_hash, content_hash}, time}` — PROV-DM
qualified-derivation shape. Granularity policy: per-instance stamp +
per-section watched-set stamps **by default**; per-row source-assertion ids on core
rows only. `content_hash` re-stamps on every state-changing render.

### 12.5 Bitemporal view versioning

Each render is a versioned instance chained by revision; system-time axis ships v1
(archived independent instances at
`notes/system/views/revisions/{doc-slug}/rev-{N}.md`; `read_asof(as_of_system)` =
instance lookup); valid-time axis (`as_of_valid` graph replay) is a named deferred
extension.

### 12.6 `view_render` op contract

`view_render(document_id, mode ∈ {register, refresh, full, read_asof}, root_id,
view_profile, narrative_sections, as_of_system, as_of_valid, agent, session_id)`.

The op renders/repairs the deterministic CORE only; narrative is preserved
verbatim on refresh. Typed errors: `document_not_found`, `view_not_registered`,
`view_root_required`, `unknown_view_profile`, `recipe_profile_mismatch`,
`anti_amnesia_violation`, `citation_grammar_violation`, `as_of_valid_unsupported`,
`as_of_instance_not_found`.

### 12.7 Staleness detection (two-tier)

Tier-0 structural = snapshot high-water + citation-liveness + core-hash,
**localized per section via watched sets**; finding payload
`{view_span, verdict, source_ids}` rides `detail` in the registry finding shape;
surfaced via the audit channel. Tier-1 claim-level (NLI/QA over narrative) = v2,
named interface `view_narrative_unsupported`, deliberately unimplemented.

### 12.8 Refresh semantics as a postulate suite

KM revision (record was wrong → repair rendering in place) vs update (world changed
→ row moves between sections); Independence/anti-amnesia postulate (a refresh
triggered by change to source A must not alter content whose watched set excludes
A); localized repair default, full re-render on broad mismatch; typed
Create/Update/Delete deltas with tombstone propagation on retire/rekey/merge (P7).
Conformance is behavioral (test suite) with deliberate rejections documented.

### 12.9 Architecture naming

Assertions + supersession = the event-sourced write model; views = read models
(CQRS split named); per-matter journal = single-publisher append-only event log;
charter = **checkpointed fold** over (journal + graph); refresh = replay of the
delta since `derived_from_snapshot`; full replay reserved for recovery; session
close = the batch-commit/checkpoint boundary (hook implementation owned by
`todo:session-close-playbook-hook`, cited not built).

---

## 13. Version history

| Version | Changes |
|---|---|
| v2.0 | Initial v2: provenance, temporal, staging |
| v2.1 | Tool consolidation (11 tools → `cortex(tool=...)`) |
| v2.2 | Session edges (Phase 1) |
| v2.3 | Session-edge reasoning graph |
| v2.4 | Write-time enforcement, commitment tracking, ingest tooling |
| v2.5 | Status-trait normalization Phase 0 (migration 050): nullable `lifecycle` / `confidence_band` / `confidence_score` / `adoption` entity traits + assertion `credibility`, shadow-populated, `status` still read-authoritative; per-type `type_confidence_fields` registry (migration 047). See `cortex:notes/system/specs/cortex-status-trait-normalization-spec-2026-06-02.md` and confidence-derivation/v2 policy. |
| v3.0 | URI scheme, BYO-storage, prospective indexing, event extraction, 2 new edge types |
| v3.1 | Derived views: view artifact class, view_render op, Tier-0 staleness detectors, derived_from structural registration, PROV qualified stamps + revision chain, view profiles as data recipes |
