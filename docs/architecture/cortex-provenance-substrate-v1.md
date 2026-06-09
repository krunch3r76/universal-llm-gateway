# Cortex Provenance Substrate — Architecture Spec v1.3

## Version history

| Version | Date | Changes | Anchor |
|---|---|---|---|
| v1.0 | 2026-05-13 | Initial spec drafted in `transcript:web-2026-05-13-0438`. §0–§11 + Appendix A,B + Resume/promote checklist. SuperHeavy Pass A/B review begins. | `document:cortex-provenance-substrate-spec-v1.0` |
| v1.1 | 2026-05-13 | Write-time discipline tightening absorbed from Pass A/B feedback: §4.7 forward-looking provenance (`prospective_summary`, `events_json`, `artifact_uri`) added; §3.4 derivation×confidence interaction clarified; §5 auditor-validatability pre-write checklist + tooling backstop subsections. | `document:cortex-provenance-substrate-spec-v1.1` |
| v1.2 | 2026-05-14 | Pass A/B audit findings absorbed: Appendix A "Lineage and belief-revision lineages" subsection added (covering provenance semirings, Datalog/TMS, CRDT-KGs, bitemporal property graphs, AGM/Hansson belief-revision); §7.3 field-preservation contract extended to cover forward-provenance fields. | `document:cortex-provenance-substrate-spec-v1.2` |
| v1.3 | 2026-05-17 | Field-grade artifact handling (§3.1 mixed-grade extension), dependency tracking (§3.5 NEW covering internal derived fields AND agent-authored fs artifacts via `derives_from` frontmatter), skill-router scope extension to derived-artifact-authoring (§8 extension), consumer obligations (§12 NEW), enforcement layer split (§13 NEW, substrate-primary + audit-backstop + reader-defense-in-depth), implementation prerequisites (§14 NEW, predicate_form backfill + pairwise supersedence-candidate detection). §7.5 redirect-to-active primitive (frictions 10156, 10158). Appendix C (Boot-time provenance surface, closes §11.7). Appendix D (Agent-time-of-use injection patterns). | `document:cortex-provenance-substrate-spec-v1.3` |
| v1.3.2 | 2026-05-17 | Appendix D wire-up amendments: (a) D.5 invariant 2 reference corrected (§12.9 → §12.13); (b) D.5 invariants 4 (admission-gated truncation) and 5 (content-hash integrity) added; (c) D.2 template extended with pagination/selection/integrity metadata (included_count, total_active_count, truncated, selection_strategy, selection_params, cursor, content_hash); (d) new §12.13 output citation grammar `[assertion:NNNN]`, multi-bracket for multi-source, and enforcement contract; (e) §8.2 extended to 18 finding kinds incl. output_citation_missing_assertion, grade_laundering_in_output, temporal_qualification_omitted, bibliography_orphan, output_citation_semantic_mismatch; (f) §3.1 adds `aggregation` derivation type + §1.1 set-entity convention; (g) §10.1 adds mandatory §6-independent adversarial review pass for brief-domain artifacts. Full verbatim amendment text in `cortex://notes/system/specs/appendix-d-v1-3-2-release-package.md`. Grammar choice and prior-art grounding panel-resolved 2026-05-17 (anthropic/claude-opus-4-7 + openai/gpt-5.5 + xai/grok-superheavy; §6 three-family independence). Phase 1.0 implementation target: `libs/cortex_store/agent_injection/`. | `document:cortex-provenance-substrate-spec-v1.3` |

Note on versioning convention: minor-version bumps (v1.x) stay in `cortex-provenance-substrate-v1.md` and update this block. Major-version bumps move the filename (`cortex-provenance-substrate-v2.md`). The `document:cortex-provenance-substrate-spec-v<version>` entities in cortex carry `supersedes`-relationships to their predecessors so the version chain is a primary-source artifact in the graph, not only in this prose.

**Retroactive anchoring:** the v1.0 / v1.1 / v1.2 entities listed above are created as part of the v1.3 ship to backfill the version chain. Their `source_uri` references the same file at the prior content hash where available; for prior versions where no content-hash anchor was kept, the entity description carries the change summary verbatim from this table.


**Status:** draft (session `claude-web-2026-05-13-1806`, continuing from `claude-web-2026-05-13-1728`; v1.2 revision continuing from audit session `claude-web-2026-05-13-1921`; v1.3.2 amendments fully applied session `claude-web-2026-05-17-0928` (continuation `claude-web-2026-05-17-1113`))
**Version:** v1.3.2
**Audience:** Cortex agents (all seats, all platforms); spec readers without Cortex internals
**Scope:** Universal write-time provenance discipline for the Cortex epistemic substrate
**Companion artifacts:**
- `document:entity-backed-claim-provenance-v1` — first domain instantiation (legal briefs / authored artifacts)
- `artifact:epistemic-substrate-paper-draft` — public-narrative research paper (Memory + Provenance + Consensus)
- `artifact:goose-grant-packet-v3` — Goose AAIF grant application narrative

**Changelog:**
- v1.3.2 — Appendix D wire-up amendments fully applied. Full text: `cortex://notes/system/specs/appendix-d-v1-3-2-release-package.md`.
- v1.2 — Adds Appendix A subsection "Lineage and belief-revision lineages," clarifying Cortex's relationship to provenance semirings, Datalog / TMS lineage models, CRDT-based knowledge graphs, bitemporal property graphs, and AGM / Hansson belief-revision substrates. Descriptive related work; no normative protocol changes. Triggered by the Q6 finding in the SuperHeavy substrate-review audit (`notes/system/threads/979-grok-superheavy-substrate-review-AUDIT.md`).
- v1.1 — Post-initial-draft refinements ahead of SuperHeavy review dispatch.
- v1.0 — Initial draft (session `claude-web-2026-05-13-1806`).

---

## Abstract

This spec defines the universal write-time discipline that produces the **provenance pillar** of the Cortex epistemic substrate. Every assertion written into the graph carries (a) a confidence label drawn from a four-level ladder, (b) a derivation-type drawn from a fixed taxonomy with per-type co-requirements, (c) an evidence string and an `evidence_uris` list, (d) an audit gate that an independent LLM auditor — with no access to the originating session's context — can run against the entity card alone to validate the confidence label, AND (e) a forward-looking projection (`prospective_summary` + `events_json`) generated at write time that makes the supersession chain a feedback corpus for future agent runs, not merely an audit trail. Independence between the originator of a claim and any verifier of that claim is enforced at family/version granularity. Supersession is governed by AGM expansion / contraction / revision semantics with a field-preservation contract. Gaps between drafted artifacts and the entities they reference are surfaced as graph artifacts by a universal gap detector covering both backward-evidence and forward-projection dimensions.

The spec is the architecture-layer formalization of the provenance pillar developed in the substrate paper (`artifact:epistemic-substrate-paper-draft`, drafted in `transcript:web-2026-05-13-0438`). Where the consensus pipeline gates **inter-model** verification on `originator_model_id != evaluator_model_id`, this spec generalizes the originator slot from "another model" to **any primary-source authority entity** — so the same independence gate, the same gap-detection primitive, and the same lineage tracking carry over to legal briefs, scientific papers, regulatory filings, medical charts, and any other domain where claims chain back to verifiable authorities. The brief-domain spec `document:entity-backed-claim-provenance-v1` is the first instantiation; this spec is the parent.

**Provenance is dual.** The *backward-looking* dimension traces every claim to its origin (evidence_uris, derivation_type, supersession chain) and produces the auditor-validatability and cross-model independence guarantees. The *forward-looking* dimension projects future relevance and event structure (`prospective_summary`, `events_json`) and produces the substrate's claim to be a *learning substrate* — the supersede chain isn't just an audit trail, it's a feedback corpus that recalibrates future agent confidence without requiring a hosted training pipeline. Kumiho's LoCoMo-Plus benchmark established the forward primitive as load-bearing: accuracy from 61.6% (similarity-only) to 93.3% (prospective-indexed) on long-horizon recall (`service:cortex` assertion 1516).

The load-bearing public claim: **structural impossibility of un-grounded `confirmed` claims by construction.** The *Mata v. Avianca* and *Park v. Kim* hallucinated-citation failure modes — and the closer-to-home single-source verbatim confabulation by SuperHeavy on BOE Annotation 625.0036 (session `web-2026-05-13-0239`) — become not "rarer with better RAG" but architecturally inaccessible. The graph refuses to consider an assertion `confirmed` if its evidence path does not satisfy the auditor-validatability gate, refuses to count two evidence sources as independent corroboration if they share family/version model identity, and flags forward-projection gaps that would degrade future-retrieval quality.

---

## § 0. Strategic frame

The architectural thesis (assertion 9149 on `project:universal-llm-gateway`) is that the consensus pipeline's core invariant — **independence + provenance + automated verification gates produce higher-quality outputs** — generalizes from inter-model verification to inter-source verification. The pipeline's `is_independent` check, its `validate_provenance_present` gap detector, and its lineage tracking all carry over unchanged. What changes is the type of the originator slot.

This spec sits in the substrate stack:

- **Memory pillar** — `spec:cortex-v2.4` defines the read model: entities, assertions, relationships, edges, projection-aware fetch, AGM-compliant supersession at the storage layer. `document:cortex-v3-spec` extends the assertion row with four forward-looking columns (`prospective_summary`, `events_json`, `artifact_uri`, `artifact_storage`) that the write discipline below treats as first-class primitives.
- **Provenance pillar** — THIS SPEC defines the write-time discipline: confidence ladder semantics, derivation-type co-requirements, evidence contracts, auditor-validatability gate, cross-model independence gate, supersession field-preservation contract, universal gap-detection, AND the forward-looking provenance primitives.
- **Consensus pillar** — pipeline architecture in `libs/provenance/` and the dispatch layer enforce inter-model independence at evaluation time. The consensus pipeline is one consumer of the provenance contract this spec defines.

**Provenance is dual: backward-looking AND forward-looking.** The backward-looking dimension is what most discussions of "provenance" mean — every claim traces back to its origin via `evidence_uris`, `derivation_type`, and the supersession chain. The §5 auditor-validatability gate and §6 cross-model independence gate enforce backward-looking discipline. The forward-looking dimension is what makes the substrate also a *feedback corpus for future agent runs* (per `artifact:epistemic-substrate-paper-draft` §3.1, asserted in `transcript:web-2026-05-13-0438`): every assertion carries, at write time, a `prospective_summary` projecting which future scenarios make the claim relevant, plus `events_json` projecting causal/temporal continuations. The Kumiho LoCoMo-Plus benchmark established the forward primitive's load-bearing role: accuracy from 61.6% → 93.3% with prospective indexing (`service:cortex` assertion 1516; `document:cortex-v3-spec`). The forward-looking primitives are NOT optional decoration — they bridge the cue-trigger semantic gap that pure-similarity retrieval misses, and they are the structural basis for the substrate's claim to be a *learning substrate*, not merely an *audit substrate*.

The spec is written for a public-artifact target: terminology is defined inline, no Cortex internals are assumed, naming is stable enough to be lifted into a paper without retrofitting. Where the spec references an existing Cortex assertion or session by ID, the reference is a durable cortex:// citation that resolves to a persisted record, not a transient log line.

The brief-domain spec instantiates this architecture for legal briefs (entity types `legal_source:`, `case-law:`, `exhibit:`; URI scheme with pinpoint fragments; citation-token gap detector; the BOE-19-P appeal as the conformance corpus). Future domain instantiations — scientific papers, regulatory filings, medical charts — follow the same pattern: take this spec's primitives, derive the domain's entity types, derive the domain's citation-token surface for §8's gap detector, write a domain instantiation document that imports rather than reinvents the gates in §5 and §6.

---

## § 1. Primitives

This section defines the four Cortex primitives at the level of contract, not implementation. The `spec:cortex-v2.4` read model handles storage and projection; this spec layers write-time discipline on top.

### 1.1 Entity

An **entity** is a typed, named node in the graph with a stable URI. Each entity carries:

- `id` — typed slug, of the form `<type>:<slug>`, globally unique and immutable once created.
- `type` — typed prefix (e.g. `legal_source`, `case`, `artifact`, `todo`, `agent_skill`, `service`, `model`).
- `name`, `description` — human-readable identity.
- `status` — entity-level workflow signal: `provisional` | `confirmed` (additional values reserved per entity type).
- `workflow_state` — typed per-type column (e.g. `todo: open | in_progress | blocked | done | deferred | cancelled`).
- `attributes` — typed attribute dict. Each value is data the entity card surfaces to readers; each load-bearing attribute on a `status: confirmed` entity must carry ≥1 backing assertion at `confidence: confirmed` (see §5.1).
- `source_uri` — optional pointer to a canonical file or URL the entity reflects. Auto-recomputes `content_hash` on update.

**Set entities — convention.** Entities with type prefix `set:` (e.g.,
`set:admin-fees-2025`, `set:exhibits-boe19p`) carry no description on
their own; they exist as the target of §1.3 `has_member` relationships
from member assertions or member entities. A `set:` entity's "content"
is the union of its member-relationships. Set entities can themselves
be the subject of assertions (a `set:` is a first-class entity), and
can be aggregated over by `aggregation`-derived assertions (§3.1) that
compute totals, counts, or summaries over their members. Inline citation
of a set's aggregate result uses the standard `[assertion:NNNN]`
grammar (§12.13) pointing at the aggregating assertion, not at the
set entity directly.

### 1.2 Assertion

An **assertion** is a typed claim about an entity, written into the graph as a row with structured provenance. Each assertion carries:

**Backward-looking provenance fields (origin + traceback):**

- `entity_id` — the entity the claim is about.
- `claim` — the claim text. For `confidence: confirmed` derived from a source, the claim text MUST embed the literal verbatim quote in quote marks (see §5.2).
- `confidence` — one of `hypothesized` | `suspected` | `believed` | `confirmed` (§2).
- `derivation_type` — one of the typed taxonomy values (§3).
- `evidence` — prose summary of how the claim was obtained.
- `evidence_uris` — list of stable, fetchable URIs pointing at the source(s) (§4).
- `chunk_id` — required for `derivation_type` in {`quotation`, `compression`} (§3.2).
- `seeded_by` — model identity of the originator (`family/version` granularity, §6.2).

**Temporal provenance fields:**

- `observed_at` — ISO timestamp; auto-fills to `now()` if absent.
- `valid_from`, `valid_until` — temporal validity window. `valid_from` REQUIRED when claim contains a date pattern unless `derivation_type` is an observation type.
- `superseded_by` — assertion ID that supersedes this one (set atomically by `supersede`; see §7).

**Forward-looking provenance fields (future relevance + downstream reasoning), per `document:cortex-v3-spec` and §4.7 below:**

- `prospective_summary` — LLM-generated forward-relevance projection. Auto-generated at write time, structured as prose describing which future scenarios make the claim retrievable / relevant. Bridges the cue-trigger semantic gap that pure-similarity retrieval misses (Kumiho LoCoMo-Plus benchmark: 61.6% → 93.3% with this enrichment; `service:cortex` assertion 1516).
- `events_json` — structured event extraction, serialized as a JSON array of `{event, consequence, temporal}` triples. Asserts the causal/temporal structure the claim implies, for downstream reasoning over the supersede chain and for substrate-level event indexing. `null` when the claim is a static fact rather than a temporally-located event.
- `artifact_uri`, `artifact_storage` — assertion-bound artifact persistence. When an assertion is associated with a generated artifact (a brief draft, a screenshot, a CSV, a session transcript), `artifact_uri` is the canonical URI and `artifact_storage` ∈ {`inline`, `external`, `cortex_sandbox`, `workspaces`} specifies the storage discipline. Default `inline` for assertions whose content fits in the claim text; `external`/`cortex_sandbox`/`workspaces` for larger artifacts.

The forward-looking fields ARE substrate primitives, not optional metadata. The §5 auditor-validatability gate applies to the backward-looking fields; the §8 gap detector covers BOTH backward and forward dimensions (see §8.2 finding kinds `missing_prospective_summary` and `events_json_invalid`).

### 1.3 Relationship

A **relationship** is a typed, directed edge between two entities — *structural*, not reasoning. Each relationship carries `source_id`, `target_id`, a `type_id` (e.g. `references`, `child_of`, `related_to`, `archives_to`), an optional `role` annotation, a `strength` weight, and provenance fields (`session_id`, `agent`, `evidence`).

Relationships represent durable structural facts about how entities relate. The same `agm`-expansion / contraction / revision discipline that governs assertions also governs relationships — soft-delete is preserved for provenance audit; deleted relationships do not appear in list views but remain in the row store.

### 1.4 Reasoning edge

A **reasoning edge** is a typed, directed, session-attributed cognitive connection seeded by an agent during reasoning. Edge types (`depends_on`, `leads_to`, `caused_by`, `contradicts`, `supersedes`, `evidence_for`, `corroborates`, `derived_from`, `extends`, `analogous_to`, `reasoned_about`, etc.) capture the structure of an agent's reasoning across nodes. Edges are NOT structural — two agents reasoning about the same pair of entities may seed different edges.

Edges are the substrate for cross-model verification (§6). When verifier `B` reads the source backing claim `C` and emits a `corroborates(B → C)` edge, the edge — together with the independence gate — is what closes the loop. Edges are also the substrate for §9's reasoning patterns: phase-closure-spawns-successor, contradicts-driven supersede, evidence-for chain construction.

---

## § 2. Confidence ladder

The confidence field on every assertion takes one of four values, ordered by strength. Promotion across the ladder is governed by what evidence is in hand; the §5 auditor-validatability gate is the bar a `confirmed` assertion must clear.

### 2.1 The four values

| Value | Semantic |
|---|---|
| `hypothesized` | Possibility raised, not yet pursued. The claim is in the graph because reasoning will reference it; the claim is not asserted to be true. Useful for capturing speculations, follow-up questions, alternative hypotheses worth keeping. |
| `suspected` | Plausibility reasoning supports it, but no source has been consulted. Pattern-match against analogous cases, prior reasoning, or default expectations. Lower confidence than `believed`. |
| `believed` | A source has been consulted and supports the claim, but the auditor-validatability gate is NOT satisfied — typically because the source was a single agent's output (a single web-search seat, a single dispatch) that has not been independently verified, OR because the verbatim is not yet embedded in the claim text, OR because evidence_uris is provisional. |
| `confirmed` | Auditor-validatability gate (§5) is satisfied AND independence requirement (§6) is satisfied. An independent LLM auditor with no access to the originating session's context can validate the claim using ONLY the entity card. |

### 2.2 Promotion paths

Promotion from `believed` → `confirmed` requires ONE of three paths:

1. **Direct fetch path.** The originating agent has done their own independent fetch of the source URI (not a paste-in from another seat) and confirmed the verbatim matches what's in the claim. The agent's `derivation_type` is `direct_observation` and the agent's seat is recorded in the evidence string.
2. **Multi-source corroboration path.** A second independent source (different authoritative origin, or a verifier model at different `family/version` granularity, §6.2) has produced corroborating evidence — typically via a `corroborates` reasoning edge — and the corroboration is reflected in the evidence string and `evidence_uris` list.
3. **Structural verifiability path.** The source is cryptographically or structurally verifiable without trusting any specific agent — e.g., a signed PDF whose signature has been validated; a git commit hash; a content-addressed artifact; a deterministic derivation from a known-good seed (e.g., `cortex resolve` against a known entity ID).

A claim cannot be promoted to `confirmed` from `believed` solely because the originating agent restates their confidence. Promotion is evidence-bound, not assertion-bound.

### 2.3 Downgrade semantics

Downgrade is always available and is the right move when the auditor-validatability gate cannot be satisfied at write time. The cost of downgrading is low: a `believed` assertion that gets promoted later is cheap. The cost of writing `confirmed` and failing an auditor's verification is high: every downstream reasoning step that trusted the assertion must be re-examined.

Downgrade is recorded by writing a new assertion at the lower confidence and `supersede`-ing the old one (§7). The supersede chain preserves both rows in the store; the old assertion's `superseded_by` field points at the new.

When downgrading, the `reasoning_summary` field on the new assertion SHOULD name the specific gate the previous version failed (e.g. "verbatim not embedded in claim text; auditor cannot grep against URI"). A future agent reading the chain can then promote when the gap is closed.

### 2.4 The single-source-confabulation problem

Single-source ingestion is the canonical promotion failure mode and the motivating case for the ladder's strict separation of `believed` from `confirmed`. A single capable agent (SuperHeavy on a web-search seat; a single Grok dispatch with web access) can pattern-complete a plausible-looking verbatim that does not exist at the cited source. The originating agent has no way to distinguish their own confabulation from their own correct fetch.

The canonical anchor: SuperHeavy paste-in returned 16 verbatims labeled VERIFIED for the BOE-19-P §9.2 bibliographic-index seeding (session `web-2026-05-13-0239`). Independent `vortex:web_fetch` spot-checks surfaced one confabulation: Annotation 625.0036's "C 6/19/2007" effective-date marker did not exist on the live BOE page; the correct effective date is 1992-02-28 per cited LTA 92/15. Pattern: when the source format does not match the agent's expected template, the agent invents a fitting date by pattern-completion from sibling sources whose format does match.

The structural fix is the confidence ladder itself plus the §5 gate: seed at `believed` from a single source, promote to `confirmed` only when one of the three §2.2 paths closes.

---

## § 3. Derivation-type taxonomy

The `derivation_type` field is a structured signal about HOW the evidence was obtained. The taxonomy is finite and fixed; per-type co-requirements are enforced at write time by the cortex API and are repeated here as normative contract.

### 3.1 The taxonomy

| Value | Meaning | Co-requirements |
|---|---|---|
| `direct_observation` | Agent directly fetched / read / deterministically derived the claim from the source. | `evidence_uris` non-empty; `evidence` string names the fetch (tool + timestamp + seat). |
| `agent_observation` | Agent received the claim from a tool whose output mediated the source (web_fetch, file read, API). The source was not directly inspected by the agent's own reasoning; the tool's output was. | `evidence_uris` non-empty; `evidence` string names the tool and the run. |
| `inference` | Agent synthesized the claim from prior context, sibling sources, or pattern-completion. | `evidence` string names the reasoning path. `evidence_uris` may point at supporting entities or transcripts but does not claim to be the source of the inferred fact. |
| `user_statement` | The user told the agent the claim directly. | `evidence` string identifies the user message (turn / session); `evidence_uris` typically `transcript:<session>` or `agent-bus:<thread>/<turn>`. |
| `quotation` | Verbatim quote of source text at chunk granularity. | `chunk_id` required (resolved via `ingest_document` → `assert_from_chunk`); `evidence_uris` MUST contain the URI of the chunk's parent source. |
| `compression` | Compression of a chunk into a derived claim that summarizes or paraphrases. | `chunk_id` required; `evidence_uris` MUST contain the parent source URI. |
| `commitment` | Agent commitment to do something in the future — the claim is performative, not descriptive. | `evidence` string identifies the commitment context. |
| `stated` | Generic stated claim with no narrower derivation_type fit; rare. | `evidence` string supplied. |
| `other` | Reserved escape hatch. | `evidence` string MUST justify why none of the above fit. |
| `aggregation` | Assertion enumerates and computes over a named `set:` entity's members, producing a derived total, count, or summary. Snapshot semantics at `computed_at`. Distinct from `compression` (lossy summary) and `quotation` (verbatim text). | `set:` entity bound via §1.3 `has_member`; claim-text MUST enumerate member assertion_ids OR a `set:` entity_id; `content_hash` covers member-id list + aggregate result. |

The taxonomy is derived from TROVE (quotation / compression / inference / other) — already Cortex's foundation per `service:cortex` assertion 101 — extended with the observation types (`direct_observation`, `agent_observation`, `user_statement`) required to handle agent-tool-mediated evidence and direct user input, plus `commitment` for performative claims and `stated`/`other` as escape hatches.


**`aggregation`** — assertion enumerates and computes over a named set
of other assertions, producing a derived total, count, or summary. Uses
§1.3 `has_member` relationships from a `set:` entity to bind members.
Distinct from `compression` (lossy summary) and `quotation` (verbatim
text). Aggregation is a SNAPSHOT at `computed_at`; member supersedence
after computation does not auto-supersede the aggregate. The member
provenance chain remains queryable via the §1.3 relationship traversal,
so a downstream consumer can detect stale aggregates by walking from
the aggregate's member list to current-controlling assertions and
diff-ing.

Required claim-text structure: aggregate claims MUST enumerate either
(a) member assertion_ids explicitly in the claim text, OR (b) a `set:`
entity_id that the consumer can resolve to members via §1.3 relationship
lookup. The aggregate's `content_hash` covers the member-id list +
aggregate result computed at `computed_at`.

### 3.1.1 Mixed-grade artifacts and inline assertion anchors

Most artifacts are uniformly graded — a `quotation` chunk is wholly evidence-grade; a session journal is wholly orientation-grade. A growing class of artifacts is **mixed**: orientation-grade as a whole but containing evidence-grade rows that make specific factual claims about substrate state. Canonical examples:

- **Case document indexes** (e.g. `legal/uber/document-index.md`) — orientation-grade table of contents with evidence-grade rows like "Authoritative source (text, current): `…reconstructed-for-docx.md`" that quote specific assertion content.
- **Master TODO descriptions** — orientation-grade goal narrative with evidence-grade rows like "Phase 0 complete per assertion 7800/7805/7807."
- **Skill documents** — orientation-grade discipline prose with evidence-grade rows like "deadline 2026-05-17 (assertion 7918)."

Without per-row grading, the supersession discipline cannot localize what is stale: the entire artifact is treated as orientation-grade and exempt from staleness propagation, or the entire artifact is treated as evidence-grade and a single stale row stales the whole document.

**Declaration mechanism — inline assertion anchors.** Evidence-grade rows in agent-authored fs artifacts carry an HTML-style comment binding the row to a specific assertion:

```markdown
- **Authoritative source (text, current):** `…reconstructed-for-docx.md` <!-- assertion:9023 -->
```

Anchors parse into the freshness check defined in §3.5. Rows without anchors are treated as orientation-grade and excluded from staleness propagation. Anchors carrying `assertion:<id>` participate in dependency tracking; if assertion 9023 is later superseded, the artifact containing the anchor is flagged stale.

**Anchor variants:**
- `<!-- assertion:9023 -->` — single load-bearing assertion
- `<!-- assertion:9023,9089 -->` — multiple assertions both supporting the row
- `<!-- assertion:9023 valid_until:2026-12-31 -->` — temporally-scoped row, auto-stales after the date
- `<!-- evidence-grade -->` (no assertion id) — row is evidence-grade but the supporting assertion has not yet been wired; flagged for completion by the author or session-close audit

**Canonical failure anchor:** the Uber security-incident-report document-index supersedence failure of 2026-05-14 (assertion 9767, file `notes/system/threads/v1.3-candidate-input-fs-artifact-supersedence-2026-05-14.md`). The "Authoritative source (text)" row quoted the older of two competing assertions (9020) when a newer assertion (9023) had already established the authoritative source three days prior. Without the inline anchor mechanism this section adds, the substrate had no way to localize which row depended on which assertion.

### 3.2 Chunk binding for `quotation` and `compression`

A `chunk_id` resolves to a contiguous span of a previously-ingested document. The ingestion path is:

1. `ingest_document(source_uri, content, observer?, source_date?)` — chunks the document at structure boundaries (headings, paragraphs, sections) and returns chunk IDs.
2. `assert_from_chunk(chunk_id, entity_id, claim, confidence, evidence, ...)` — writes an assertion bound to a specific chunk.

For `derivation_type: quotation`, the claim text contains the literal verbatim from the chunk, in quote marks, and the chunk-id binding gives an auditor a deterministic way to fetch the exact passage the claim is quoting. For `derivation_type: compression`, the claim text summarizes/paraphrases the chunk and the chunk-id binding gives the auditor the source span the compression must be faithful to.

A `quotation` assertion whose claim text does not actually contain the quoted span is a structural-field-vs-claim-text mismatch and is the §5.2 audit failure mode rendered at the structured-field level. A `compression` assertion whose claim asserts facts beyond what the chunk supports is the §5.5 failure mode rendered at the structured-field level.


> **v1.4 erratum (recorded 2026-05-22, plan:cortex-v3-completion Phase A):**
>
> The two-step ingestion path above (`ingest_document` → `assert_from_chunk`) is **superseded** by Cortex decision #10504 + user-statement assertion #10750 (session `claude-web-2026-05-22-2209` turn 3, three-way reviewed on bus thread 1051). The **architectural requirement** of this section — chunk-bound auditor fetch for `quotation` and `compression` claims — is preserved and now fulfilled by **RAG-deterministic chunk IDs** of the form `{content_hash_prefix}-{i}` (see `services/rag/chunkers.py` and `services/rag/rag_service/_indexing_embed.py:75`; auditor-fetch primitive lives at `services/rag/rag_service/api.py:120-180` as `POST /chunks_by_index`).
>
> What survives: `assertions.chunk_id` column (redefined semantics — references RAG-deterministic IDs); §3.2's auditor-fetch contract; the chunk-bound discipline for `derivation_type: quotation` and `derivation_type: compression`.
>
> What is retired (plan:cortex-v3-completion Phase E executes the drop): cortex-internal `chunks` table; `cortex(tool=ingest_document)` op; `cortex(tool=assert_from_chunk)` op; `routes/ingest.py`; `ingest_chunker` module.
>
> Read-time discipline until Phase E lands: agents writing `quotation` / `compression` assertions should treat `chunk_id` as a RAG-deterministic ID and cite the parent source via `evidence_uris` per §4.2's URI-pair mandate. Resolver hook is Phase E scope (cortex-side; uses existing RAG `/chunks_by_index`).
>
> Erratum recorded ahead of v1.4 promotion (precedent: §7.5.4 "Optional v1.4 candidate" pattern). Canonical record: assertion seeded on this entity in Phase A; tracking entity: `plan:cortex-v3-completion`.

### 3.3 Field-vs-prose consistency contract

The derivation_type field is a structured signal that downstream readers (the auditor, the consensus pipeline's `validate_provenance_present`, future agents traversing the graph) trust as a fast classifier. The contract is: **the structured field must MATCH the prose in the evidence string.**

The canonical violation: an assertion with `derivation_type: direct_observation` whose evidence string says "format-pattern inference from sibling fetches." That's the structured field claiming "I directly read the source" while the prose admits "I inferred from a sibling." The structured field will be trusted by downstream consumers; the prose will be skimmed. The auditor catches the mismatch only on close reading.

The fix is a pre-write self-check: before calling `assert`, the agent reads back the (derivation_type, evidence) pair and asks "if a stranger only sees this structured field, does it match what the prose admits?" If not, change the structured field to match — most often, `direct_observation` → `inference` — and downgrade confidence accordingly.

### 3.4 Derivation type and confidence interact

Not every derivation type can support every confidence level. The interaction matrix:

| `derivation_type` | Max supported `confidence` (default) | Notes |
|---|---|---|
| `direct_observation` | `confirmed` | Requires §5 gate satisfied. |
| `agent_observation` | `confirmed` | Requires §5 gate satisfied AND the mediating tool is in scope of `direct_observation`-class trust (deterministic, non-confabulating). For LLM-mediated tools (a SuperHeavy paste-in via grok web), max default is `believed` pending §2.2 promotion. |
| `inference` | `believed` | `confirmed` requires §2.2 multi-source corroboration or structural verifiability path. Single-agent inference cannot reach `confirmed`. |
| `user_statement` | `confirmed` | The user is the source; user statements may be confirmed by virtue of direct user authorship. |
| `quotation` | `confirmed` | Requires `chunk_id` resolved and verbatim grep-able against URI. |
| `compression` | `confirmed` | Requires `chunk_id` resolved and compression faithful to chunk. |
| `commitment` | `confirmed` | The commitment exists by virtue of being made. |
| `stated`, `other` | `believed` | `confirmed` only with explicit justification in evidence. |

The matrix is normative for the §5 pre-write checklist: a write at `confidence: confirmed` whose derivation_type's max-supported is `believed` is the audit failure mode regardless of how compelling the evidence prose sounds.



---


### 3.5 Dependency tracking on derived artifacts

The derivation-type taxonomy in §3.1–§3.4 governs the **write** of each assertion — at the moment of write, what kind of derivation was performed and what evidence backs it. This section defines the complementary discipline for **read** and **re-read** of artifacts that summarize or quote assertion content over time. Without this discipline, an artifact can survive correctly through the moment of authoring and silently go stale as the underlying assertions are superseded.

**Scope.** Dependency tracking applies to any artifact that quotes or summarizes substrate content, regardless of storage location:

- **Internal derived fields** — `summary_row`, `journal prose`, `prospective_summary`, the boot card's `Last Session` summary. The 2026-05-15 scrubbed-claim-survives-in-summary_row regression (assertion 9761, §X consumer-obligations input) is the canonical failure of this surface.
- **Search and RAG outputs** — already covered in §7 by retrieval-time recomputation. Listed here for completeness; §7 is the operational spec.
- **Agent-authored fs files** — case document indexes, master-TODO descriptions, skill documents, system specs that quote substrate assertions. The 2026-05-14 Uber security-incident-report doc-index supersedence failure (assertion 9767) is the canonical failure of this surface.
- **Outbound artifacts** — email drafts, message-bus turn bodies, generated reports, slide content that quotes substrate state. v2 scope; the v1.3 enforcement targets the first three.

**Declaration mechanism — `derives_from` frontmatter and inline anchors.** Artifacts declare their dependencies in one of two compatible ways:

1. **Whole-artifact declaration** — frontmatter block at the top of the file:

```yaml
---
derives_from:
  - assertion: 9023
    fact: authoritative_source_for_docx_security_incident_2026-05-07
    last_verified: 2026-05-14T13:48:00Z
  - assertion: 9089
    fact: submission_status_security_incident_report
    last_verified: 2026-05-14T13:48:00Z
---
```

2. **Per-row declaration** — inline anchors per §3.1.1, for mixed-grade artifacts where dependency is localized to specific rows rather than the whole document.

Both are normalized into the same internal representation: a set of `(artifact_uri, assertion_id, anchor_locator, last_verified)` tuples.

**Freshness check.** A walk-and-flag pass over the dependency tuples:

1. For each `(artifact_uri, assertion_id, …)` tuple:
   - If `assertions[assertion_id].superseded_by IS NOT NULL` → artifact carries a stale dependency.
   - If `assertions[assertion_id].valid_until IS NOT NULL AND valid_until < now()` → artifact carries an expired dependency.
   - Else → tuple is fresh.
2. Flag artifacts with ≥1 stale or expired dependency as `freshness: stale`.
3. Surface stale artifacts to readers (boot card, retrieval skill, consuming agent) before the artifact's content is acted on.

**Re-verification.** When a reader updates an artifact's row to reflect new substrate state, the `last_verified` timestamp updates and the anchor or frontmatter entry rebinds to the current (non-superseded) assertion id. The supersession chain itself remains intact in the substrate — only the artifact's pointer moves.

**Enforcement layer:** see §13 for the substrate-primary / audit-backstop / reader-defense-in-depth split. §3.5 defines the contract; §13 defines which layer enforces it.

**Anti-patterns:**
- *Implicit dependency.* Quoting assertion content without an anchor or frontmatter entry. The discipline is observability — un-anchored quotes cannot participate in staleness propagation.
- *Stale-tolerant artifact.* Marking the whole artifact `freshness_check: false` to suppress the walk. Permitted only for artifacts that are intentionally time-locked (archived correspondence, point-in-time snapshots) — these declare `point_in_time: <ISO>` so the freshness pass treats them as canonically frozen.
- *Anchor without `last_verified`.* The anchor is incomplete — the reader has no way to know whether the binding was made before or after the cited assertion's most recent supersession event.

## § 4. Evidence semantics

The `evidence` string and `evidence_uris` list together carry the assertion's grounding. This section defines the contract on each, the description-claim backing requirement, and the temporal-validity discipline.

### 4.1 The `evidence` string

The `evidence` string is prose summarizing how the claim was obtained. It is NOT a free-form annotation; it is a structured prose claim that downstream auditors will read against the (derivation_type, evidence_uris) pair to validate consistency.

The minimum content of an `evidence` string at `confidence: confirmed`:

- The tool used (e.g. `vortex:web_fetch`, `vortex:fs(op='read')`, `vortex:rag`, `cortex(tool='entity_get')`, direct paste-in from a user message at session timestamp X).
- The agent seat that ran the tool (e.g. "claude-web", "cursor", "claude-api"). At `confidence: confirmed` the seat identity matters: the seat is what an auditor would re-run to reproduce the fetch.
- The timestamp of the fetch (ISO format).
- Whether the fetch was a primary read or a paste-in. Paste-ins (user pasting a SuperHeavy paste of a web search) are NOT primary reads and the `derivation_type` should be `agent_observation` with the paste-in mediator identified, not `direct_observation`.

### 4.2 The `evidence_uris` list

`evidence_uris` is a list of stable, fetchable URIs pointing at the source(s) the claim derives from. Each URI must satisfy:

- **Stable** — the URI resolves to the same content on re-fetch within the validity window. URIs that 302-redirect to a different document, or that paywall after a window, are not stable.
- **Fetchable** — a downstream agent with the same tool surface can fetch the URI without bespoke credentials. (Where credentialed fetches are unavoidable, the credential pattern must be documented in the entity's evidence string and a `service:` entity for the credential source must be referenced.)
- **Addressable to the claim** — for chunk-bound assertions (`quotation`, `compression`), the URI must address the chunk's parent source; the chunk_id then handles pinpointing within the source.

Acceptable URI schemes:

- `https://` — public web resource. Authoritative versions preferred (leginfo.legislature.ca.gov for California statutes, official court reporters for case law, journal DOIs for papers).
- `cortex://` — resolvable cortex resource (entity ID, chunk ID, assertion ID, transcript ID, file path). Resolved via `cortex(tool='resolve', arguments={'uri': ...})`.
- `transcript:<session-id>` — points at a recorded session transcript on disk. Used for `user_statement` and for session-derived inferences.
- `agent-bus:<thread>/<turn>` — points at a specific inter-agent message bus turn.
- `workspaces:<repo>/<path>` — workspace file path.

`evidence_uris: null` or `evidence_uris: []` at `confidence: confirmed` is the structural form of the §5 audit failure mode: the auditor has nowhere to point their own verification at. The cortex write surface flags this case as a `validation_warning` at write time (`todo:cortex-write-time-auditor-validatability-hints`, commit `5ad3910d` 2026-05-13).

### 4.3 Verbatim embedding for confirmed-quotation claims

For `derivation_type: quotation` at `confidence: confirmed`, the verbatim string must be embedded in the claim text in quote marks, not merely referenced. This is the operational §5.2 requirement repeated here at the evidence layer.

**Bad:** `"SuperHeavy verbatim of leginfo footer identical to § 7000"` — describes where to find the verbatim, doesn't embed it. The auditor cannot grep against the URI.

**Good:** `"Leginfo verbatim (directly fetched by claude-web 2026-05-13T03:43Z): '(Amended by Stats. 2025, Ch. 539, Sec. 1. (SB 293) Effective January 1, 2026.)'"` — embeds the literal text in quote marks. The auditor fetches the URI, greps for the quoted footer, confirms match.

The verbatim normalization spec (whitespace collapse, smart-quote folding, header/footer stripping) is in §11.6 as v2 work. v1 enforces exact byte-for-byte match modulo trivial whitespace.

### 4.4 Description-claim backing requirement

The entity's `description` field reads as orienting prose, but the auditor treats it as a set of factual claims. Every factual claim in the description on a `status: confirmed` entity must ALSO be backed by an assertion meeting the §5 gate.

The most common violation: an entity description asserts "(enacted 2021-02-16 by Constitutional initiative)" but no assertion on the entity references Cal. Const. Art. XIII A § 2.1(c) as the source. The auditor reads the entity card holistically — the description's load-bearing claims need backing.

The fix at write time is either:
- Add a backing assertion for the description's claim, OR
- Soften the description from a factual assertion to clearly orienting language (e.g., "Constitutional initiative provision — see assertion N for date").

### 4.5 Temporal validity — `valid_from` / `valid_until`

When a claim contains a date pattern (YYYY-MM-DD, ISO timestamp, named date) the cortex write surface REQUIRES `valid_from` unless `derivation_type` is an observation type (`direct_observation`, `agent_observation`, `user_statement` — for these, the observation IS the dated event, and `observed_at` carries the timestamp).

`valid_from` is the start of the claim's validity window — typically the operative date of the asserted fact (effective date of a statute, decision date of a case, signing date of a document). `valid_until` is the end, set by a subsequent supersede or by a known sunset.

The temporal validity discipline is what lets the consensus pipeline answer "what was true at time T?" questions. An assertion at `confidence: confirmed` without a clear validity window leaves time-aware queries unable to filter; the cortex API's `valid_from`-requirement-when-date-pattern-detected is the structural backstop.

### 4.6 Lineage

For derived claims, the `lineage` attribute (OpenLineage-compatible serialization) records the chain of inputs: which assertions were read by the agent during reasoning, which tool outputs fed in, which prior reasoning edges were traversed. Lineage is what makes a `compression` faithful — the auditor can walk back from the compressed claim to the chunks it summarized.

In v1, lineage is reserved as an optional attribute pattern for chunk-derived assertions and consensus-pipeline outputs; full lineage instrumentation is in `service:cortex` and the consensus pipeline. v2 will formalize the serialization (see §11).

---


### 4.7 Forward-looking provenance — `prospective_summary` and `events_json`

The forward-looking provenance fields are the substrate's structural mechanism for treating the supersede chain as both an audit trail AND a feedback corpus for future agent runs. The framing is established in `artifact:epistemic-substrate-paper-draft` §3.1 (Memory pillar): *"The supersession chain is the structural heart... This record has two functions: audit trail for the human operator, and feedback corpus for future agent runs. The pattern of superseded hypotheses on similar tasks recalibrates confidence before the agent commits to a new claim, without requiring a hosted training pipeline."* The forward-looking primitives operationalize the second function.

#### 4.7.1 `prospective_summary` — semantics and contract

`prospective_summary` is an LLM-generated prose projection, written at the same time as the assertion, of which future scenarios make the claim relevant. It is NOT a paraphrase of the claim. It answers a different question: *"When will a future agent need this?"* — naming the context, the trigger, the kind of query for which this fact, retrieved by similarity alone, would have been missed.

Contract:

- **Generated at write time.** The cortex write surface generates `prospective_summary` automatically when an assertion is written. The LLM call producing it has access to the claim text, evidence, derivation_type, and the entity's existing assertion set.
- **Auto-regenerated on supersede.** When a `supersede` revises the claim, the new row's `prospective_summary` is regenerated for the revised claim (the old row's `prospective_summary` is preserved per §7.3 field-preservation; superseded rows carry their original projection).
- **NOT subject to the §5 verbatim-embedding requirement.** prospective_summary is a meta-claim about future relevance, not a load-bearing factual claim about the world. R2 (verbatim quote) does not apply.
- **Subject to the §8 gap detector.** A `confidence: confirmed` assertion with `prospective_summary: null` is flagged by §8.2 finding kind `missing_prospective_summary` (severity: low — non-blocking but reduces future-retrieval quality).
- **Bridges the cue-trigger semantic gap.** Pure-similarity retrieval misses the case where the user's future query uses different vocabulary than the original claim — e.g., the claim says "Cal. R&T § 63.1" and a future query asks "parent-child exclusion." `prospective_summary` is the column the substrate retrieves against for cue-trigger matching, alongside the claim text.

The empirical foundation: Kumiho's LoCoMo-Plus benchmark established that prospective indexing eliminates the >6-month accuracy cliff in similarity-only retrieval, with accuracy rising from 61.6% (similarity-only baseline) to 93.3% (prospective-indexed) on long-horizon recall tasks (Kumiho paper §15.3; `service:cortex` assertion 1516; `document:cortex-v3-spec`).

#### 4.7.2 `events_json` — semantics and contract

`events_json` is a structured serialization of the causal/temporal events the claim implies, as a JSON array of `{event, consequence, temporal}` triples. It is `null` for claims that are static facts (definitions, type assertions, attribute values without temporal structure) and populated for claims that record happenings (an email arriving; a status change; a deadline closing).

Contract:

- **Generated at write time** alongside `prospective_summary`, by the same LLM call.
- **Each triple is atomic and decontextualized.** The triple `{event, consequence, temporal}` must be readable in isolation by a future agent traversing the substrate. Multi-event claims produce multiple triples.
- **`temporal` field can be ISO timestamp, named-date, or `null`** (when the claim's structure is causal but not anchored to a specific time).
- **Subject to §8 gap detector.** An assertion whose claim text describes a temporally-located event but has `events_json: null` is flagged by §8.2 finding kind `events_json_invalid` (severity: medium). An assertion whose `events_json` contains a triple inconsistent with the claim text (event doesn't match; consequence contradicts; temporal off by more than the validity window) is also flagged.

#### 4.7.3 `artifact_uri` / `artifact_storage` — artifact persistence

When an assertion is associated with a generated artifact larger than the claim text can carry (a session transcript, a brief draft, a screenshot, a CSV, a PDF), `artifact_uri` is the canonical URI and `artifact_storage` ∈ {`inline`, `external`, `cortex_sandbox`, `workspaces`} specifies the storage discipline. The decision matrix:

| Storage | When to use |
|---|---|
| `inline` | Claim content fits in the `claim` text; no separate artifact exists. (Default.) |
| `external` | Artifact lives at a public URL (e.g., a published paper at a DOI); URI is the public address. |
| `cortex_sandbox` | Artifact lives at a path under `/data/files/` (cortex sandbox); URI is `cortex://...` |
| `workspaces` | Artifact lives at a path under workspaces (`/mnt/torus/projects/`); URI is `workspaces:...` |

`artifact_uri` is treated as a `evidence_uris`-class URI for the §5 R3 requirement: an auditor verifying the assertion can fetch the artifact to validate the claim.

#### 4.7.4 Relationship to the consensus pipeline

The forward-looking primitives feed the consensus pipeline directly. When a verifier model is dispatched to corroborate or contradict a claim, the verifier receives the prospective_summary alongside the claim text — surfacing which future scenarios the originator thought the claim would matter for. A `contradicts` reasoning edge from the verifier can target either the claim itself OR the prospective_summary's projection (e.g., "this claim is correct as stated but the prospective_summary mis-predicts the future relevance — the actual scope is narrower"). The substrate distinguishes these two contradiction modes at the edge layer.

#### 4.7.5 Origin

The forward-looking primitives derive from Kumiho's graph-native cognitive memory architecture (LoCoMo-Plus accuracy benchmark §15.3) and were deployed to Cortex in v3.0 migration 019 (`document:cortex-v3-spec` assertion 1517, four new columns added 2026-04-05). The three-pillar epistemic substrate framing (Memory + Provenance + Consensus) developed in session `web-2026-05-13-0438` (`artifact:epistemic-substrate-paper-draft`) made forward-looking provenance load-bearing for the public-artifact narrative: the substrate is a *learning substrate*, not merely an *audit substrate*.

## § 5. Auditor-validatability gate

The auditor-validatability gate is the universal write-time discipline for `confidence: confirmed`. It is the formalization of the rule Kaywan stated 2026-05-13T04:18Z after the SuperHeavy confabulation on BOE Annotation 625.0036:

> **Whatever entity is designated `confidence: confirmed`, an independent LLM auditor with no access to the originating session's context must be able to validate the confidence label using ONLY the entity card (attributes + assertions + relationships).**

This section codifies the gate as a typed contract. The complementary skill `agent_skill:auditor-validatable-confidence` carries the per-agent operational discipline; this section is the architecture-layer normative spec.

### 5.1 The five operational requirements

For an assertion at `confidence: confirmed` (and by extension `status: confirmed` on the parent entity), the assertion MUST satisfy all five:

**R1. Every typed attribute on a `status: confirmed` entity carries ≥1 backing assertion at `confidence: confirmed`.** An entity with `status: confirmed` and a typed attribute (`effective_date`, `decision_date`, `citation_canonical`, `pinpoint_default`, `original_effective_date`, etc.) MUST carry, for each such attribute, at least one assertion at `confidence: confirmed` whose claim text supports the attribute value. An unsupported attribute on a confirmed entity is the audit failure mode at the attribute layer.

The corollary: if a single assertion's claim text supports two attributes, the assertion's claim must explicitly cover both. If one assertion's evidence only covers one attribute on a multi-attribute entity, write a second assertion for the second attribute. Attributes do not ride on neighbors' evidence.

**R2. Verbatim quote embedded in the claim text, in quote marks, from the authoritative source.** For claims at `confidence: confirmed` derived from a source (`direct_observation`, `agent_observation`, `quotation`, `compression`), the literal source text the claim depends on MUST be in the claim text, in quote marks. Descriptions of where the quote is do not satisfy R2.

**R3. Source URI in `evidence_uris`.** The `evidence_uris` list MUST contain at least one URI per §4.2 pointing at the authoritative source the verbatim came from. The URI must be a stable, fetchable address. `evidence_uris: null` or `evidence_uris: []` with `confidence: confirmed` fails R3 structurally.

**R4. `derivation_type` accurately reflects the actual evidence path.** Per §3.3 field-vs-prose consistency contract: the structured `derivation_type` field MUST match the prose in the `evidence` string. The auditor reads the structured field as a fast classifier; the prose must not contradict it.

**R5. Description claims are also backed by assertions.** Per §4.4: every load-bearing factual claim in the entity's `description` field must be backed by an assertion on the entity meeting R2–R4.

### 5.2 The single-source-insufficient corollary

R1–R5 are necessary but not sufficient for `confidence: confirmed`. The single-source-insufficient corollary (§2.4) further requires that the evidence path satisfy one of the three §2.2 promotion paths: direct fetch, multi-source corroboration, or structural verifiability.

The structural form of the corollary at the assertion layer: an assertion whose evidence string identifies only one agent (`SuperHeavy paste-in`, `Grok web seat dispatch`, `Gemini frontier_dispatch`) and only one source fetch CANNOT be written at `confidence: confirmed` even if R1–R5 are satisfied. The corollary downgrades the assertion to `confidence: believed` with a note in `reasoning_summary` identifying the single-source provenance.

The cortex API enforces R3 (URI presence) and R4 (field-vs-prose consistency at the structured level) at write time as `validation_warning`s. The single-source corollary is enforced by the §6 independence gate — see §6.

### 5.3 Pre-write checklist

Before calling `assert(confidence='confirmed')` (or `entity_create(status='confirmed')`, or `supersede` with `confirmed`), an agent MUST run the following internally:

1. [ ] Is there a verbatim quote in the `claim` text, in quote marks, from the authoritative source? (R2)
2. [ ] Is the source URI in `evidence_uris`? (R3)
3. [ ] Does `derivation_type` match the actual evidence path? (R4 — compare against the evidence string; if mismatch, fix the structured field.)
4. [ ] Was the evidence path independent — direct fetch, OR multi-source agreement, OR cryptographically/structurally verifiable? (§2.2 promotion paths; if only single-source seat output, downgrade to `believed`.)
5. [ ] If the parent entity will carry `status='confirmed'`, does every typed attribute have a backing assertion? (R1 — walk the attributes dict; for each key, confirm an assertion exists.)
6. [ ] Does the entity description make any factual claim that isn't covered by an assertion? (R5 — soften or add assertion.)

If ANY check fails, two options remain available:

- **Fix the gap** — fetch the verbatim, expand `evidence_uris`, correct `derivation_type`, write the missing backing assertion, THEN write at `confirmed`.
- **Downgrade** — write at `believed` (or `suspected` if even weaker). Note the gap in `reasoning_summary` so a future agent knows what to promote and what evidence to gather.

Downgrade is always cheaper than confirm-then-cleanup.

### 5.4 Tooling backstop — write-time validation warnings

The cortex write surface ships advisory `validation_warning`s when an agent attempts to write `confidence: confirmed` without satisfying the structural requirements (R3 URI presence, R4 structured-field-vs-prose consistency at the field level). The warnings are advisory in v1 (`mode: warn`, non-blocking) so the principle is enforced as discipline first and as code second; v2 may promote selected gates to blocking once the false-positive rate is characterized.

The tooling enhancement is filed under `todo:cortex-write-time-auditor-validatability-hints` and shipped 2026-05-13 in commit `5ad3910d` (agent-bus thread 978). The session-close audit (`todo:auditor-validatability-retroactive-audit`) is the complementary retroactive pass that applies the gate to pre-2026-05-13 confirmed assertions.

### 5.5 Anti-patterns

The following are the canonical R1–R5 violations, named so agents can recognize and avoid them:

- **"See the URL" claim shortcut.** Claim text says "verbatim available at the URI"; auditor must fetch separately to see the verbatim. Violates R2. Fix: embed the verbatim.
- **Cross-reference shortcut.** Claim text says "verbatim identical to sibling entity X." Violates R2 AND risks the cross-reference itself being wrong. Fix: embed the verbatim directly from the source.
- **Field-vs-prose mismatch.** `derivation_type: direct_observation` but evidence string says "inferred from sibling fetches." Violates R4. Fix: change structured field to `inference` and downgrade confidence if needed.
- **Single-seat confidence promotion.** Assertion at `confidence: confirmed` whose evidence string identifies a single agent paste-in. Violates §5.2 corollary. Fix: downgrade to `believed` OR add a §2.2 promotion path.
- **Confirmed entity with unsupported attribute.** Entity has `status: confirmed` and typed attribute `effective_date: 2024-04-01` but no assertion on the entity references that date. Violates R1. Fix: write the backing assertion or downgrade the entity.
- **Description with un-backed factual claim.** Description asserts "(enacted 2021-02-16)" but no assertion supports the date. Violates R5. Fix: write backing assertion or soften description.

### 5.6 Canonical failure anchor

The motivating case for the gate: session `web-2026-05-13-0239`, BOE-19-P §9.2 Bibliographic Index seed verification pass. SuperHeavy paste-in returned 16 verbatims for BOE annotations labeled VERIFIED. Independent `vortex:web_fetch` spot-checks surfaced one confabulation — Annotation 625.0036's "C 6/19/2007" effective-date marker did not exist on the live BOE page; the correct effective date is 1992-02-28 per cited LTA 92/15. The agent had no way to distinguish their own confabulation from their own correct fetch by inspecting their own output. Only an independent verifier (the auditor, a second model, the agent themselves running a second tool) can close that loop.

The gate retroactively applied to that session's 10 freshly-seeded `legal_source:` entities surfaced three structural gaps:
- assertion 9703 (`legal_source:probate-code-7001`): claim described verbatim by cross-reference instead of embedding → R2 violation; fixed via 9712.
- assertion 9701/9710 (`legal_source:rtc-5151`): supersede shortcut silently dropped `evidence_uris` / `derivation_type` / `valid_from` → R3+R4 violation at the supersede layer; fixed via 9711; tool fix filed under `todo:cortex-supersede-thin-signature-field-loss` (closed 2026-05-13T17:26Z).
- `legal_source:rtc-63.2` `original_effective_date: 2021-02-16` attribute had no backing assertion → R1 violation; fixed by writing 9714 citing Cal. Const. Art. XIII A § 2.1(c).

The principle is captured in cortex as assertion 9715 on `document:entity-backed-claim-provenance-v1` (the first domain instantiation's entity) and codified in this section as universal architecture-layer discipline.



---

## § 6. Cross-model independence gate

The independence gate enforces that the originator of a claim and any verifier of that claim are independent at family/version model granularity. This section generalizes the brief-spec §5.2 independence gate from inter-model verification to the universal substrate, and resolves the family/version-vs-seat granularity question canonically.

### 6.1 The gate

Before a verifier writes a corroborating or contradicting assertion (or seeds a `corroborates` / `contradicts` reasoning edge), the gate runs:

```python
def is_independent(target_provenance, verifier_model_id) -> bool:
    """
    target_provenance: the originator's provenance record for the claim under verification.
        Specifically, target_provenance.seeded_by carries the originator's model identity.
    verifier_model_id: the model identity of the agent attempting verification.

    Both identities are normalized to family/version granularity per §6.2.
    Returns True iff originator and verifier are at different family/version, OR
    target_provenance specifies a primary-source authority (non-model) origin.
    """
    if target_provenance.origin_kind == "authority_entity":
        # Originator is a primary-source authority (legal_source, exhibit, etc.),
        # not another model. Any model verifying against the authority is independent
        # by construction — the authority does not have a model identity.
        return True

    originator_normalized = normalize_to_family_version(target_provenance.seeded_by)
    verifier_normalized = normalize_to_family_version(verifier_model_id)
    return originator_normalized != verifier_normalized
```

The gate returns False — verification is NOT independent and must NOT count as corroboration — when the verifier and originator share family/version. This applies regardless of seat, platform, dispatch shape, or session.

### 6.2 Family/version granularity

The independence check compares model identity at the **family/version** level, not at the seat or platform level. Same model on different seats does NOT satisfy independence.

The normalization:

| Input model identity | Normalized `family/version` |
|---|---|
| `anthropic/claude-opus-4-7` (web seat) | `anthropic/claude-opus-4-7` |
| `anthropic/claude-opus-4-7` (cursor seat) | `anthropic/claude-opus-4-7` |
| `anthropic/claude-opus-4-7` (api seat) | `anthropic/claude-opus-4-7` |
| `openai/gpt-5.5` (any seat) | `openai/gpt-5.5` |
| `openai/gpt-5.1` (any seat) | `openai/gpt-5.1` |
| `google/gemini-2.5-pro` (any seat) | `google/gemini-2.5-pro` |
| `google/gemini-3-pro` (any seat) | `google/gemini-3-pro` |
| `xai/grok-superheavy` (web seat) | `xai/grok-superheavy` |
| `xai/grok-superheavy` (api seat) | `xai/grok-superheavy` |

Family and version BOTH matter. `gpt-5.5` and `gpt-5.1` are independent. `claude-opus-4-7` and `claude-opus-4-6` are independent. Different seats of the same family/version are NOT independent.

The motivating case is the brief-spec's own three-reviewer consult (`agent-bus:968`, session `web-2026-05-12-2121`): Claude Opus 4.7 on cursor was added as a "third reviewer" of a draft authored by Claude Opus 4.7 on web. The cursor seat substantively converged with the gpt-5.5 reviewer on Q3 and Q5, but the convergence does NOT constitute independent corroboration of the drafter — same model identity at family/version, regardless of platform.

The existing `libs/provenance::is_independent` was audited during the brief-spec's §9.1 schema-registration phase to confirm normalization compares at family/version granularity rather than session/seat granularity. The brief-spec's §5.3 edge-endpoint caveat (`originator_model_id` versus `seat_id` on the reasoning-edge endpoints) is the specific implementation-side detail of how this normalization is enforced.

### 6.3 The three independence paths

§2.2 named three promotion paths from `believed` to `confirmed`. The independence-gate restatement of those paths:

1. **Direct fetch path** — the originating agent's own fetch of the source URI is the evidence. The originator's model identity is in `seeded_by`; verification is by the auditor independently re-fetching the URI. Independence is between the agent (as originator) and the auditor (as verifier model class).
2. **Multi-source corroboration path** — a second independent source corroborates. The second source can be (a) a second independent authoritative origin (e.g., the same statute fetched from a different official mirror), OR (b) a verifier model at different family/version corroborating against the same source. Either way, the gate runs at family/version normalization.
3. **Structural verifiability path** — the source is cryptographically/structurally verifiable without trusting any specific agent. Independence is trivial — the verification has no model dependency.

### 6.4 Dispatch-shape priming sensitivity

A subtle independence-adjacent failure mode: the **same** model can produce different answers on the **same** substrate depending on dispatch shape (MCP loop vs frontier-inline; whether the substrate is included as a bus-message vs as an inlined file; whether the model has been primed by prior turns in the same MCP loop). The canonical anchor: `google/gemini-2.5-pro` on the brief-spec consult (thread 968) returned different Q6 answers across dispatch shapes — bus-posted MCP-loop replies returned the soft-flag option (b); `frontier_dispatch` with `mcp=False` and inline substrate returned the hard-fail option (a).

Implication: even with the family/version independence gate satisfied, two verifications dispatched against the same model under different shapes are NOT guaranteed to be replicable. v1 treats this as an open issue (§11.3); v2 will specify a canonical dispatch shape for verifier consults.

### 6.5 Same-model panel non-independence

A panel of reviewers convened to evaluate a draft fails the independence gate if any panel member shares family/version with the drafter. The brief-spec's three-reviewer consult is the canonical failure: two strictly independent reviewers (`openai/gpt-5.5`, `google/gemini-2.5-pro`) plus one same-model reviewer (`anthropic/claude-opus-4-7` cursor seat) is a TWO-vote independent panel, not a three-vote panel. The cursor seat's convergence with gpt-5.5 strengthens the two-vote view but does not add an independent third.

The disclosure requirement: when a same-model reviewer is included in a panel (deliberately, for converged-perspective input), the panel report MUST disclose the non-independence at the top of the reviewer's contribution and the panel's aggregate confidence MUST be computed against the independent count only. Cursor's turn-5 reply on thread 968 followed the disclosure protocol — disclosed non-blind read at the top, signed with model identity. The architectural fix going forward is the `is_panel_independent` check (deferred to §11.5 v2 work).

---

## § 7. Supersession discipline

Supersession is how the substrate maintains AGM compliance — expansion, contraction, and revision of beliefs over time — without information loss. This section formalizes the supersession contract and the field-preservation invariant.

### 7.1 AGM mapping

The substrate distinguishes three modes of belief change, mapped to AGM operations:

| Mode | AGM | Substrate operation |
|---|---|---|
| **Expansion** | Add a claim consistent with existing beliefs | `assert(...)` writes a new assertion. No supersede. |
| **Contraction** | Remove a claim (e.g., learned it was unsupported) | `supersede(old, new)` with new=null-claim, OR `assertion_update(superseded_by=null, valid_until=now)` |
| **Revision** | Replace a claim with a contradicting one | `supersede(old, new)` with new asserting the corrected claim |

Both contraction and revision use the supersede primitive; expansion uses plain assert. The substrate's AGM compliance is the foundation for time-aware queries: "what was believed at time T?" filters by `valid_from`/`valid_until` windows across the supersede chain.

The AGM commitment is recorded in `service:cortex` assertion 1854 (Kaywan directive 2026-04-08): "Cortex is AGM-compliant; supersession preserves both rows; the chain is queryable." This spec formalizes the field-preservation contract that operationalizes the commitment.

### 7.2 The atomic supersede

The `supersede(old_assertion_id, entity_id, claim, confidence, evidence, session_id, agent)` operation is atomic:

1. The old assertion's `superseded_by` is set to the new assertion's ID.
2. The new assertion is written.
3. Both writes succeed-or-fail together. A partial supersede is not a valid substrate state.

The atomicity is what makes supersede chains queryable: a reader walking the chain can rely on every row's `superseded_by` either being null (current) or pointing at a successor that exists.

### 7.3 The field-preservation contract

The atomic supersede MUST preserve all provenance fields from the superseded assertion that are not explicitly being revised. Specifically:

| Field | Preserved by supersede | Revisable explicitly |
|---|---|---|
| `entity_id` | Always preserved | No |
| `claim` | Revised in supersede | Always (this is the point of supersede) |
| `confidence` | Revisable | Yes |
| `derivation_type` | **MUST be preserved if not explicitly revised** | Yes, when evidence path changes |
| `evidence` | Revisable | Yes |
| `evidence_uris` | **MUST be preserved if not explicitly revised** | Yes |
| `chunk_id` | **MUST be preserved for quotation/compression unless source changes** | Yes |
| `seeded_by` | Update to current agent | (Implicit — supersede records the new originator) |
| `session_id` | Update to current session (the supersede event has its own session) | (Implicit — supersede records the new session) |
| `valid_from` | **MUST be preserved if not explicitly revised** | Yes, when validity window changes |
| `valid_until` | Set by the supersede (the old assertion's window closes) | — |
| `observed_at` | Set to now() on the new row | — |
| `reasoning_summary` | Revisable, often to name the gap the prior row failed | Yes |
| `lineage` | **MUST be preserved if not explicitly revised** (the supersede inherits the predecessor's lineage and extends it; the new row's lineage references the superseded row as an input) | Yes, when the derivation chain genuinely changes (not just the conclusion) |
| `prospective_summary` | **AUTO-REGENERATED on supersede** (the new claim has different future-relevance projection); the superseded row's `prospective_summary` is preserved on its own row for audit | Yes (implicit — regeneration is automatic) |
| `events_json` | **AUTO-REGENERATED on supersede** (if the new claim's event structure differs); preserved on the superseded row | Yes |
| `artifact_uri` / `artifact_storage` | **MUST be preserved if not explicitly revised** (the artifact may still be the canonical referent even when the claim text is revised) | Yes, when the artifact is replaced |

The canonical violation: the cortex `supersede` MCP tool's thin signature regression (`todo:cortex-supersede-thin-signature-field-loss`, fixed 2026-05-13T17:26Z). The thin-signature wrapper accepted only (`old`, `entity_id`, `claim`, `confidence`, `evidence`, `session_id`, `agent`) and silently dropped `evidence_uris`, `derivation_type`, `valid_from`, `chunk_id` on the new row. The result: an assertion superseded into `confidence: confirmed` whose new row had `evidence_uris: null` and `derivation_type` defaulted, failing R3 and R4 of §5.1. The same regression would have silently dropped `prospective_summary` and `events_json` on the new row had they been part of the legacy thin signature.

The fix at the API surface: `supersede` accepts the full assertion field set and explicitly preserves any field the caller does not revise. Callers who want to revise a subset pass only the subset; preservation is automatic. The forward-looking primitives (`prospective_summary`, `events_json`) are the special case where preservation behavior diverges from the other fields: they auto-regenerate by default because the future-relevance projection is a function of the (now revised) claim, not of the predecessor's claim. The legacy projection is preserved on the superseded row for audit; the current row carries a fresh projection.

### 7.4 Audit trail

The supersede chain is the substrate's primary audit trail. Reading the chain in temporal order shows:

- What was believed at each time.
- When and why beliefs changed (`reasoning_summary` on each successor names the change).
- What evidence was in hand at each step (`evidence`, `evidence_uris`).
- Which agent made the change (`seeded_by`).

The chain is preserved permanently — soft-deletes preserve the row for provenance audit even when the row no longer appears in list views. A subsequent agent investigating a downstream decision can walk back through the chain to understand what was known and when.

The `valid_until` field on the superseded assertion is set to the same `observed_at` as the successor's writing, anchoring the temporal window cleanly. Time-aware queries `WHERE valid_from <= T <= valid_until` then filter correctly.

### 7.5 Supersede vs assertion_update

For non-content changes — review status, valid_until on a current assertion, reviewer attribution — use `assertion_update` (in-place patch) rather than `supersede` (new row). The distinction:

- `assertion_update`: metadata-only change, no new claim, no new evidence path. In-place patch.
- `supersede`: claim or evidence path changes; revision or contraction. New row.

A supersede that only changes review_status is wasteful; an assertion_update that changes the claim text is a silent rewrite that loses the supersede chain. The boundary is normative.

---


### 7.5.1 The third pattern — redirect to a pre-existing active row

Beyond the two cases the original §7.5 defines (assertion_update for metadata-only patches; supersede for claim/evidence revisions producing a NEW row), there is a third structural case the original prose did not surface:

**Redirect-to-active.** The "supersedor" row already exists in the corpus as an active assertion. The operation is purely a pointer update: set `old.superseded_by = existing_new.id`. No new row, no claim revision, no evidence path change. The substrate primitive for this is **`assertion_update(assertion_id=<old>, superseded_by=<new>, valid_until=<now>)`**, not `supersede(...)`.

This pattern is the dominant shape in:
- §14.2(a) **literal-match pair supersedes** — both rows already exist; verdict identifies which is older.
- §14.2(b) **semantic-similarity pair supersedes** — same: both rows exist; verdict picks ordering.
- §14.2(c) **cluster_merge retirements** — N members of a cluster, one elected canonical; the other N−1 redirect to canonical.

In all three cases, calling `supersede(old, claim=new.claim, entity_id=new.entity_id, ...)` is **wrong** because supersede creates a NEW row Z carrying `new.claim` and points `old.superseded_by → Z`. The active `new` row remains untouched, and Z becomes a literal duplicate of `new` on the same entity. The supersession chain is intact but the corpus carries a stranded duplicate that must be cleaned up with a second supersede (Z → new), a Class 1 chain rewrite, or a Class 5 fallback recovery — friction 10156 surfaced this 4× in a single §14.2(b) §A wave-1 application and 2× as residual from prior §14.2(a) reruns.

### 7.5.2 Decision matrix (replaces the §7.5 prose chart)

| Case | Primitive | Notes |
|---|---|---|
| Metadata-only patch on a row (review_status, valid_until, reviewer, review_notes, predicate_form patch, etc.) — claim unchanged | `assertion_update` | Original §7.5 case. |
| Claim or evidence revision; "new" row does NOT exist yet | `supersede` | Original §7.5 case. Creates the new row atomically with the redirect. |
| Redirect to a row that ALREADY exists active in the corpus (pair supersede, cluster retirement) | `assertion_update(old, superseded_by=existing_new.id, valid_until=now)` | The third pattern, friction 10156. The substrate has no separate "redirect" primitive — `assertion_update.superseded_by` IS the redirect primitive. |

### 7.5.3 Per-write provenance on retirement writes (friction 10158 resolution)

`assertion_update` accepts `review_notes` but **does not accept `reasoning_summary`**. Work orders that template per-write provenance as `reasoning_summary='...'` on retirement writes fail silently (the field is dropped server-side) and ship retirement writes with zero per-write provenance — only the inherited reasoning_summary on the original row.

The discipline:
- Per-write retirement provenance goes in **`review_notes`** (mutable, audit-trail-preserved, accepted by `assertion_update`).
- Batch-level provenance (work-order section reference, cluster-discovery method, canonical-selection reasoning) goes in the **§14.2(c) ledger entry** referencing the work order.
- `reasoning_summary` is reserved for the row's original derivation reasoning, written via `cortex.assert` or `cortex.supersede` at creation. It is intentionally immutable post-creation: a retirement is not a new derivation event; the source row's reasoning still applies.

### 7.5.4 Optional v1.4 candidate — `supersede_redirect` sugar primitive

A future `cortex.supersede_redirect(old, new)` primitive would wrap `assertion_update(old, superseded_by=new, valid_until=now())` with semantically clearer naming and would emit a distinct `cortex.redirect` event (separable from generic metadata patches in observability). The current `assertion_update` path is functionally complete; the new primitive is **NOT required for v1.3 sign-off** and is deferred to v1.4 scope. If added, it composes onto §7.5.2 row 3 with the same semantics.

## § 8. Universal gap-detection primitive

The universal gap-detection primitive generalizes the brief-spec §7.1 structural-gap detector from citation-token surfaces (Bibliographic-Index refs, italicized case names, "Exhibit N" labels) to ANY relationship between a drafted artifact (or seeded entity set) and the backing entities it references.

### 8.1 The detector contract

The detector takes as input:

- **Subject** — an artifact, a section of an artifact, an entity, or a typed entity set (e.g., "all `legal_source:` entities seeded in session X").
- **Reference surface** — a configuration for what reference tokens to extract from the subject. For artifacts, this is the citation-token surface; for entity sets, this is the typed-attribute set; for graph traversals, this is the relationship-type set.

It returns a list of `GapFinding` records, each with:

- `subject_id` — what was being checked.
- `token` or `attribute` or `relationship` — which reference triggered the finding.
- `kind` — the finding kind from §8.2 enum.
- `severity` — `critical` | `high` | `medium` | `low` from §8.3 ordering.
- `evidence` — the structured rationale for the finding (what was expected; what was found; pointers to remediating action).

### 8.2 The eighteen finding kinds

The detector classifies gaps along three axes: **claim-layer** (artifact-to-graph), **seed-data-layer** (entity-to-backward-evidence), and **forward-provenance-layer** (assertion-to-forward-projection). The eighteen kinds:

**Claim-layer (artifact references the graph):**

1. `missing_backing_entity` — claim token (citation, named entity, exhibit ref) does not resolve to any entity in the graph. **Severity: high.**
2. `unverified_entity` — token resolves to an entity, but the entity has zero assertions. The shell exists; the substance does not. **Severity: medium.**
3. `unverified_claim` — token resolves to an entity with assertions, but no `corroborates` reasoning edge from an independent verifier exists for the specific claim being made. **Severity: low.**
4. `contradicted_claim` — token resolves and assertions exist, but a `contradicts` reasoning edge from an independent verifier flags the specific claim. **Severity: critical.**
5. `verbatim_check_failed` — (EXTENDED in v1.3.2) triggers in TWO contexts:
   (a) artifact-side (existing): token's quotation in the artifact does not match the chunk_id-bound assertion's claim text after §4.3 normalization.
   (b) output-side (NEW): a consumer response makes a quoted statement attributed to a cited assertion (via §12.13 grammar) and the quoted text does not match the chunk_id-bound assertion's claim text after §4.3 normalization.
   **Severity: high** (both contexts).

**Seed-data-layer (entity references backward evidence):**

6. `missing_attribute_backing` — entity at `status: confirmed` has a typed attribute with no backing assertion (§5.1 R1). **Severity: high.**
7. `missing_evidence_uri` — assertion at `confidence: confirmed` has `evidence_uris: null` or empty (§5.1 R3). **Severity: high.**
8. `derivation_type_mismatch` — assertion's `derivation_type` does not match the prose in `evidence` (§5.1 R4). **Severity: medium.**
9. `description_unbacked_claim` — entity's description makes a factual claim with no backing assertion (§5.1 R5). **Severity: medium.**

**Forward-provenance-layer (assertion-to-forward-projection):**

10. `missing_prospective_summary` — assertion at `confidence: confirmed` has `prospective_summary: null` (§4.7.1). **Severity: low** — the claim is verifiable backward but unindexed for future cue-trigger retrieval; not blocking but degrades future-recall quality. The cortex write surface auto-generates `prospective_summary` by default, so this finding flags either (a) pre-v3 assertions not backfilled, or (b) write-time auto-generation failures.
11. `events_json_invalid` — assertion's `events_json` is either (a) `null` when the claim text describes a temporally-located event, or (b) populated but with a triple inconsistent with the claim text (event doesn't match; consequence contradicts the claim; temporal off by more than the assertion's `valid_from`/`valid_until` window). **Severity: medium** — invalid event structure compromises downstream causal-reasoning over the supersede chain.

**Claim-layer (consumer output references the graph) — v1.3.2 additions:**

13. `output_citation_missing_assertion` — a consumer response (per §12.5 ledger) contains a load-bearing claim whose inline citation either is absent, does not resolve to any assertion, or resolves to a superseded/retired assertion. Detected by the §12.13 output validator. **Severity: high** when on a structural-grade claim; **medium** when on a belief-grade claim with envelope-resolvable downgrade.

14. `output_citation_high_cardinality` — a single claim in §12.5 ledger has ≥ N supporting_assertion_id rows (default N=8, configurable per domain). Surface as authoring-smell warning; does not block dispatch. Suggests decomposition (per PaperTrail atomic principle, Appendix A) or sampling with explicit "e.g." framing, or migration to set-citation via the §3.1 `aggregation` derivation type if the claim is genuinely cumulative. **Severity: low.**

15. `grade_laundering_in_output` — a structural-grade output claim cites a belief-grade or `user_statement`-derived assertion without the §12.3 permitted-language framing ("One inference is X" / "User stated X"). Detected by the §12.13 output validator comparing the cited assertion's grade/derivation_type to the output prose's grade. **Severity: high.**

16. `temporal_qualification_omitted` — an output claim about a dated event cites an assertion carrying `valid_from` but the output prose omits the date binding (e.g., "the notice was timely" instead of "the notice issued YYYY-MM-DD [assertion:N] was timely"). Detected by the §12.13 output validator when cited assertion has `valid_from` and output paragraph contains no normalized date. **Severity: medium.**

17. `bibliography_orphan` — for derived-artifact-authoring domains (currently §10.1 legal briefs; extends to scientific papers and regulatory filings in v2 scope), an artifact's bibliography contains entries not referenced in body text, OR body text references bibliography indices not present in the bibliography. Detected by the §12.13 output validator running a bibliography-to-body cross-reference pass. **Severity: medium.**

18. `output_citation_semantic_mismatch` — a consumer response cites an assertion via §12.13 grammar, the citation resolves to an active assertion, but a semantic-similarity check (via §14.2(b) embedding infrastructure) between the brief's claim text and the cited assertion's `claim` text falls below the configured threshold (default 0.65, tunable per domain). Catches the "cited authority does not substantively support the proposition" failure class (BOE-19-P v5 examples: *Williams & Fickett* cited for due-process when the case is about exhaustion; R&T § 5142 cited as interest authority when § 5151 is the interest statute; BOE Annotations 190.0014 / 625.0036 cited for relation-back when neither holds that). **Severity: high.**

    Implementation: requires §14.2(b) semantic-similarity infrastructure on the cited assertion + the output claim text. Computed at output-validation time per §12.13. Threshold tuning is a Phase 1.5 deliverable using the BOE-19-P v5→v6 correction ledger as labeled training data.

    Honesty contract: this finding kind is a probabilistic check. False-positive on legitimate paraphrase and false-negative on semantically-similar-but-doctrinally-distinct citations are both possible. The §10.1 mandatory adversarial review pass is the deterministic backstop; finding 18 reduces the review pass's workload, not its necessity.

### 8.2.12 Mixed-grade artifact authored without inline anchors

**Finding kind:** `mixed_grade_no_anchors`
**Trigger:** An agent writes an fs artifact that contains rows pattern-matching the evidence-grade signature (specific factual claims about substrate state — entity IDs, assertion IDs, timestamps, deltas) without inline `<!-- assertion:<id> -->` anchors per §3.1.1 or whole-artifact `derives_from:` frontmatter per §3.5.
**Severity:** medium. Stale-dependency risk is high but containment is per-artifact, not graph-wide.
**Detector:** matches against patterns including:
- "Authoritative source (current/text/canonical)" with a filesystem path
- "as of YYYY-MM-DD" or "as of <timestamp>" with a state claim
- Numbered "Phase N complete" / "Step N done" with no anchor
- Quoted substrate fields ("`status: done`", "`workflow_state: open`") without anchor

**Routing — skill triggers:** the skill-router scope extends beyond file-ingest patterns (e.g. `agent-skills/document-lifecycle-tracking.md` Layer 2, per assertion 9766) to **derived-artifact-authoring** patterns. When an agent is about to write an fs file containing substrate quotes or summaries, the skill should fire a §3.5 freshness scan over the assertions the agent intends to quote, BEFORE the write completes.

**Peer cases:**
- Dropbox-ingest scope failure (assertion 9766 Layer 2): `cortex://dropbox/` URI failed to fire `document-lifecycle-tracking` because the trigger vocabulary was abstract. The skill was updated to match URI patterns explicitly.
- Uber doc-index authoring failure (assertion 9767): `document-lifecycle-tracking` did not fire on "author canonical retrieval index for case-evidence-retrieval skill consumption" because the trigger vocabulary covered file-ingest only, not file-authoring. Same fix shape — extend trigger vocabulary to cover both directions.

**Detector pseudocode** (extension to §8.4):

```
for artifact in fs.list(sandbox=cortex, pattern="**/*.md"):
    content = fs.read(artifact)
    if not has_frontmatter(content, key="derives_from"):
        rows = extract_rows_matching_evidence_signature(content)
        unanchored = [r for r in rows if not has_inline_anchor(r)]
        if len(unanchored) > 0:
            emit_finding(
                kind="mixed_grade_no_anchors",
                artifact_uri=artifact.uri,
                row_count=len(unanchored),
                severity="medium",
                suggested_action="add inline anchors per §3.1.1 or whole-artifact derives_from per §3.5",
            )
```

### 8.3 Severity ordering

The publish gate (where one exists — see §10 for domain-specific publish gates) refuses to render on any `critical` or `high` finding. `medium` findings are surfaced as warnings but do not block. `low` findings are surfaced in the gap report.

Severity ordering reflects what each finding kind means for downstream consumers of the artifact or entity:

- **critical** — there is positive evidence the claim is wrong (contradicted_claim).
- **high** — there is structural absence of evidence required for confidence (missing entity, missing URI, missing attribute backing, verbatim mismatch).
- **medium** — there is structural absence of consistency between fields (unverified entity, derivation_type mismatch, description un-backed).
- **low** — there is absence of corroborating verification (unverified_claim).

### 8.4 Detector pseudocode

```python
def detect_gaps(subject, reference_surface, parent_context=None) -> list[GapFinding]:
    """
    Walk the subject, find reference tokens via the configured surface, check each
    resolves and is properly backed. Surface-specific token extraction is delegated
    to the reference_surface implementation (citation tokens for artifacts;
    typed attributes for entities; relationship endpoints for graph traversal).
    """
    findings = []

    if subject.kind == "artifact":
        tokens = reference_surface.extract_citation_tokens(subject, parent_context)
        for token in tokens:
            entity = resolve_to_entity(token, parent_context)
            if entity is None:
                findings.append(GapFinding(token=token, kind="missing_backing_entity", severity="high"))
                continue
            if entity.assertion_count == 0:
                findings.append(GapFinding(token=token, kind="unverified_entity", severity="medium"))
                continue
            if has_contradicts_edge_from_independent_verifier(subject, token, entity):
                findings.append(GapFinding(token=token, kind="contradicted_claim", severity="critical"))
                continue
            if section_quotation_assertion_flagged(subject, token, entity):
                findings.append(GapFinding(token=token, kind="verbatim_check_failed", severity="high"))
                continue
            if not has_corroborates_edge_from_independent_verifier(subject, token, entity):
                findings.append(GapFinding(token=token, kind="unverified_claim", severity="low"))

    elif subject.kind == "entity":
        for attr_key, attr_value in subject.attributes.items():
            if not has_backing_assertion(subject, attr_key, attr_value):
                findings.append(GapFinding(attribute=attr_key, kind="missing_attribute_backing", severity="high"))
        for assertion in subject.assertions_at_confirmed():
            if not assertion.evidence_uris:
                findings.append(GapFinding(assertion_id=assertion.id, kind="missing_evidence_uri", severity="high"))
            if derivation_type_inconsistent_with_evidence(assertion):
                findings.append(GapFinding(assertion_id=assertion.id, kind="derivation_type_mismatch", severity="medium"))
        for desc_claim in extract_factual_claims_from_description(subject.description):
            if not has_backing_assertion_for_description_claim(subject, desc_claim):
                findings.append(GapFinding(description_claim=desc_claim, kind="description_unbacked_claim", severity="medium"))

    elif subject.kind == "entity_set":
        for entity in subject.entities:
            findings.extend(detect_gaps(entity, reference_surface, parent_context))

    return findings
```

The implementation lives in `service:cortex` (the audit endpoint) and is invoked by `cortex(tool='audit', subject=..., kinds=...)`. Phase 1b of the cortex graph-projection-and-audit-primitives arc shipped the graph-only default; `include_filesystem=true` adds fs-side checks for source_uri presence.

### 8.5 Resolution: alias lookup and parent-scope context

For tokens that don't directly match entity IDs, the detector walks the surface_forms cache (the resolution cache from `cortex(tool='surface_forms')`) and the alias chain. For brief-domain citation tokens, the `parent_case` context (passed via `parent_context`) scopes Exhibit-N tokens to `exhibit:<case-slug>/<exhibit-slug>` resolution; `legal_source:` and `case-law:` tokens are resolved globally. For other domain surfaces, the equivalent parent-scope context is defined by the domain instantiation.

### 8.6 Findings surface as graph artifacts

A gap-detection run emits findings as graph artifacts (a `gap_finding:` entity with assertions naming the finding kind, severity, and remediating action). The findings are queryable; subsequent reruns can verify that prior findings have been resolved. The discipline: a found gap is not a transient log entry; it is a persisted reasoning state that survives until remediated and explicitly resolved (via `supersede` of the finding's `open` assertion with a `resolved` successor).

### 8.7 The structural-impossibility guarantee

The full v1 contract — confidence ladder (§2), derivation-type co-requirements (§3), evidence semantics (§4), auditor-validatability gate (§5), independence gate (§6), supersession discipline (§7), and the universal gap-detection primitive (§8) — together render two failure modes structurally inaccessible at the substrate layer:

- A `confidence: confirmed` claim cannot exist without satisfying R1–R5 and §6 — the write surface rejects it (or downgrades it advisorily; v1 is `mode: warn`).
- A claim referenced in an artifact cannot resolve to "nothing in the graph" without §8's `missing_backing_entity` finding flagging it.

The *Mata v. Avianca* failure mode (hallucinated citation: a brief cites a case that does not exist) is rendered as a `missing_backing_entity` finding at severity `high`; the publish gate refuses to render. The *Park v. Kim* failure mode (citation that resolves but was never fact-checked) is rendered as `unverified_entity` (severity `medium`) or `unverified_claim` (severity `low`); the publish gate surfaces these as warnings. The SuperHeavy single-source confabulation failure mode is rendered as a `missing_evidence_uri` or `derivation_type_mismatch` finding when the agent attempts to write at `confirmed` without satisfying §5.



---

## § 9. Workflow patterns

The §5–§8 gates are write-time discipline; this section names the multi-session workflow patterns that operationalize them across long-running arcs. The patterns are encoded in `agent_skill:` entities as operational discipline; this section names them at the architecture-spec level so future agents have a single reference for the canonical workflow shapes.

### 9.1 Master-todo-with-tangent-tracing

A multi-phase arc — a spec drafting, a domain backfill, a verification pass — is anchored by a **master todo** entity at type `todo:`. The master entity's description names: the strategic frame, the active sequence (rolling cursor — updated as work progresses), the close criteria, and any tangential workstreams spawned during the arc.

Tangential workstreams are tracked as separate `todo:` entities related to the master via `related_to` with `role` annotation naming the tangent's nature ("tangential workstream — universal-layer hygiene, non-blocking"). Tangents are non-blocking to master closure; they close independently.

The master's active sequence is updated by `entity_update(description=...)` as work progresses (not by writing new assertions for each sequence change — the sequence is part of the entity's identity, not its claim history). Specific decisions, ratifications, and milestone outcomes are written as assertions on the master.

The pattern's value: a future agent reading the master's entity card gets the full arc state — current phase, what's done, what's tangent, what closes the arc — without having to walk the session history.

This spec is itself the canonical example: `todo:cortex-provenance-substrate-spec` is the master, with `todo:entity-backed-claim-provenance-implementation` as `child_of` (first domain instantiation), and `todo:auditor-validatability-retroactive-audit` + `todo:multi-phase-arc-master-todo-pattern-skill` as `related_to` tangents.

### 9.2 Phase-closure-spawns-successor

Closing a phase in a multi-phase arc — marking a `plan_phase:` entity `done`, or marking a phase-todo `done` — MUST atomically spawn the successor phase as a new entity (or explicitly mark the arc as `done` if no successor remains). A closed phase with no successor and no arc-closure assertion is a phase-closure-spine gap and is the §9.2 failure mode this discipline patches.

The canonical failure anchor: §9.1→§9.2 phase-closure spine gap surfaced in session `claude-web-2026-05-13-1728` — a phase todo closed without spawning the successor, leaving the multi-phase arc state in the session journal's open_items only. The arc-state was not durable in the graph; only the journal's open_items prose carried the continuation, and journal open_items are NOT surfaced in the boot briefing (per assertion 8384, handoffs are end-of-chat artifacts for manual copy-paste, not boot orientation).

The structural fix: phase closure is a two-write atomic operation — close the phase, create the successor (or mark the arc done). The spawning is enforced by the workflow's discipline; a future tooling enhancement may surface the gap as a `phase_closure_spine_gap` finding in §8's universal gap detector.

### 9.3 Session-close discipline

A session closes with `cortex(tool='session_close', ...)`, which atomically validates the transcript, writes it to the canonical path, creates a `transcript:` entity, writes a session journal row, and creates a `continues` edge from the prior session. The session-close is also the seam at which post-close audit findings surface (per `agent_skill:session-close-audit`).

The session-close discipline:

1. **Surface the transcript_entity_id** to the user. Subsequent assertions seeded from the session use this entity ID as their `evidence_uris` reference.
2. **Capture the handoff prompt** in `handoff_prompt` — a reflective journal entry linked to the transcript. Handoffs are user-facing artifacts (the user copy-pastes them into the next session); they are NOT auto-surfaced at boot (per assertion 8384).
3. **Run the post-close audit** — `cortex(tool='audit', subject=session_id)` surfaces gap findings; non-blocking warnings are carried forward as open todos for the next session's enrichment pass.
4. **Sparse-entity enrichment pass** — entities created during the session with zero assertions are flagged; the next session is expected to seed at least one inference assertion grounding them in the transcript.

The discipline is captured in `agent_skill:session-close-discipline` (operational) and `agent_skill:session-close-audit-discipline` (audit-side); this section names it at the architecture-spec level so the workflow has a referent in the universal spec.

### 9.4 Continues-edge and boot orientation

The substrate maintains continuity across sessions via `continues` edges between session entities and the boot-briefing pull mechanic that surfaces the last session's journal in the boot card. The discipline:

- Every session_close writes a `continues` edge from the closing session to the prior session.
- Boot pulls the last journal + the last session's `continues` chain to anchor orientation.
- The handoff prompt — agent-authored continuation — is NOT auto-surfaced (assertion 8384); the user copy-pastes it explicitly. The handoff is the user's tool, not the boot's.

The structural commitment: boot orientation is a derived projection over the session graph, not a free-form summary. A future boot improvement may surface specific handoff prompts on user request via `GET /boot-continuity` (in the boot manifest already), but the default is to keep boot lean and let handoffs serve as explicit-recall artifacts.

---

## § 10. Domain applications

This section catalogs the domains this universal spec instantiates, with pointers to the per-domain spec or scoping notes. v1 has one full instantiation (legal briefs / authored artifacts); the others are scoping pointers for v2+ work.

### 10.1 Legal briefs and authored artifacts — first instantiation

`document:entity-backed-claim-provenance-v1` (`workspaces:universal-llm-gateway/docs/architecture/entity-backed-claim-provenance.md`) is the first domain instantiation of this universal spec. The instantiation:

- Defines domain entity types: `legal_source:` (abstract primary authority), `case-law:` (cited precedent), `exhibit:` (case-specific factual document).
- Defines the URI scheme with pinpoint fragments (section / paragraph / page / line) for resolving citations to chunk granularity.
- Configures the §8 universal gap detector's reference-surface for legal-brief citation tokens (Bibliographic-Index refs, italicized case names, "Exhibit N" labels, bare statute citations).
- Specifies the migration & rollout phases (schema registration → Bibliographic Index seeding → exhibit seeding → backfill → verification → publish gate).
- Names `case:boe19p-flintridge-appeal-2026` as the v1 conformance corpus.

Per the restructure of 2026-05-13T17:58Z (session `claude-web-2026-05-13-1728`), the brief-spec's § 9.2.5 amendment (which originated the auditor-validatability principle) was hoisted to this spec's § 5 + § 6 rather than being instantiated domain-locally. The brief-spec's § 9.2.5 is now a thin pointer to this spec's § 5 + § 6 (landed v1.2). The brief-spec is therefore a pure domain instantiation, with cross-domain discipline carried by this spec.

**Mandatory adversarial review pass before filing.**

A brief-domain artifact (e.g., property-tax appeal brief, demand letter,
regulatory filing draft) MUST receive a §6-independent adversarial
review pass before it is filed with a tribunal, opposing party, or
regulatory authority.

**Minimum independence:** the reviewer's family/version (per §6.2
granularity) MUST be distinct from every authoring model that produced
load-bearing content in the brief. Different version of the same family
is INSUFFICIENT per §6.5 (same-family-non-independence).

**Recommended panel composition:** for maximum rigor under §6.2,
dispatch a two-family review panel. Example compositions:
- Brief authored by openai/gpt-5.5 → review by anthropic/claude-opus-4-x
  AND xai/grok-* (two-family).
- Brief authored by anthropic/claude-opus-4-7 → review by openai/gpt-5.x
  AND xai/grok-* (two-family).
- Brief authored by mixed dispatch (multiple families) → review by the
  family least represented in authoring, plus a context-breadth pass
  via xai/grok-superheavy.

Single-reviewer minimum is acceptable but is the weaker form of the
gate.

**Review-pass task definition:** the reviewer's prompt MUST direct the
review to:
(a) **Proposition-fit** — does the cited authority actually support the
    specific proposition the sentence makes, or only an adjacent one?
(b) **Remedy-fit** — for each item in Relief Requested, does the cited
    statute/regulation actually authorize that remedy?
(c) **Citation drift** — are reporter, page, jurisdiction, court level,
    and amendment date all accurate against the primary source?
(d) **Quote fidelity** — are quoted strings verbatim against the
    primary source file?
(e) **Factual support** — does each load-bearing date, address,
    recipient, value, and chronology trace to an exhibit, OCR sidecar,
    or confirmed Cortex assertion?
(f) **Internal consistency** — does the bibliography match the in-text
    citations? Does the relief sequence match the argument structure?

The BOE-19-P v6 handoff packet
(`dropbox/cortex_legal/2026-05-03/boe-filing/handoff-packet-extra-high-review.md`)
is the CANONICAL REFERENCE TEMPLATE for this prompt structure. When
adapting to non-cursor dispatch surfaces, the substantive criteria
(a)–(f) and the corpus-bound source-verification requirement are
preserved; the cursor-specific framing is replaced with the dispatch
target's framing.

**Dispatch surface:** review passes are dispatched via the substrate's
standard dispatch infrastructure (the `_dispatch` tool family, or
grok-build for build-coupled review). External review surfaces (cursor,
others) are ACCEPTABLE but NOT REQUIRED.

**Audit anchor:** each review pass produces a findings document keyed
to the brief version under review. The findings document SHOULD be
ingested as a `document:` entity with assertions backing each finding,
so subsequent brief versions inherit the audit trail via the supersede
chain.

**Honesty contract:** this requirement codifies the v5→v6 BOE-19-P
correction pattern. Without an independent review pass, the dominant
failure mode (wrong-assertion semantic-mismatch — see §8.2 finding 18)
is uncaught by deterministic automation. With the review pass + the
finding 18 probabilistic check (Phase 1.5), the same pattern that
closed v5→v6 closes future brief cycles.

Filing without an independent review pass is a §8 finding kind
`brief_filed_without_independent_review` (added separately if needed;
treated as policy violation rather than detector finding for v1.3.2).

### 10.2 Scientific papers (v2 scope)

The expected v2 domain instantiation for scientific papers:

- Entity types: `paper:` (peer-reviewed paper with DOI), `preprint:` (preprint with arXiv-style ID), `dataset:` (versioned dataset with content hash), `figure:` / `table:` (sub-paper-granularity references).
- URI scheme: `https://doi.org/...`, `https://arxiv.org/abs/...`, dataset content-hash URIs.
- Reference surface: in-text citation markers (`(Smith et al., 2024)`, `[15]`, `[Smith2024]`), figure/table references, dataset references.
- The §6 independence gate applies directly: replication studies are independent verifiers; multi-source citation is multi-source corroboration.

### 10.3 Regulatory filings (v2 scope)

The expected v2 instantiation for regulatory filings (SEC, FDA, EPA, etc.):

- Entity types: `regulation:` (codified rule), `guidance:` (agency guidance document), `filing:` (the regulated party's submission), `comment:` (public comment record).
- URI scheme: agency-specific (e.g., `https://www.sec.gov/Archives/edgar/...`, `https://www.federalregister.gov/...`).
- Reference surface: regulation citations, statutory authority cross-references, prior-filing cross-references.
- The §5 gate is critical: regulatory filings have legal effect; un-grounded confident assertions in a filing are legal exposure.

### 10.4 Medical charts (v2 scope, sensitive)

The expected v2 instantiation for medical charts is deferred pending the resolution of:

- PHI handling at the URI layer (HIPAA constraints on evidence_uris).
- The boundary between clinical fact (lab result, vital sign) and clinical inference (diagnosis, treatment recommendation) in the derivation_type taxonomy.
- The reviewer-independence gate when reviewers are constrained by licensure or specialty.

The domain is named here because the architectural fit is strong, but no instantiation work is in scope for v1.

### 10.5 Financial analyses (v2 scope)

The expected v2 instantiation for financial analyses (investment research, portfolio modeling, allocation decisions):

- Entity types: `security:`, `filing:` (10-K, 10-Q, etc.), `market_data_source:`, `model:` (the analysis model itself).
- Reference surface: ticker references, SEC filing references, market data citations.
- The §6 independence gate is directly applicable: cross-analyst corroboration, model-independent verification.

---

## § 11. Open issues for v2

Items deferred from v1, with names and scoping notes. Each becomes a v2 work item with its own implementation arc.

### 11.1 Auditor-validatability retroactive audit

Apply the §5 gate retroactively to pre-2026-05-13 `confidence: confirmed` assertions. The audit is non-trivial because (a) the rule was articulated 2026-05-13 and prior assertions were not held to it, (b) the population is large (thousands of confirmed assertions), and (c) the remediation cost varies per assertion. Filed as `todo:auditor-validatability-retroactive-audit`. The retroactive audit is the natural companion to the §5.4 write-time tooling — together, they close the discipline across both new and existing data.

### 11.2 Dispatch-shape canonicalization

The same model on the same substrate can produce different answers depending on dispatch shape (MCP loop vs frontier-inline; bus-message substrate vs inlined-file substrate; with/without prior-turn priming in the same loop). Canonical anchor: `google/gemini-2.5-pro` Q6 answer on thread 968. v1 does not specify a canonical dispatch shape for verifier consults; v2 will.

### 11.3 Cross-model independence at sub-version granularity

The §6.2 family/version normalization treats `claude-opus-4-7` as a single identity across all sub-versions and dates. In practice, model behavior can shift across minor revisions, fine-tunes, and serving infrastructure. v2 may introduce a `model:` entity per (family, version, training_date, serving_endpoint) tuple, with `is_independent` configurable to require independence at that finer granularity for high-stakes verifications.

### 11.4 Verbatim normalization spec

§4.3 requires exact byte-for-byte verbatim match. In practice, sources have trivial differences (whitespace collapse, smart quote vs straight quote, header/footer artifacts, OCR noise). The brief-spec §10.6 names this as a v2 item; v2 will define a canonical normalization pipeline (whitespace collapse to single-space, smart-quote folding to straight quotes, header/footer stripping per source-type, OCR noise tolerance via approximate-match with a documented threshold).

### 11.5 `is_panel_independent` check

§6.5 requires manual disclosure when a same-model reviewer is included in a panel. v2 will introduce an `is_panel_independent` check that runs the §6 normalization across all panel members and emits a non-independence finding if any pair shares family/version. The check integrates with the consensus pipeline's panel-evaluation path.

### 11.6 Lineage serialization (OpenLineage-compatible)

§4.6 reserves the `lineage` attribute; v2 will formalize the serialization. OpenLineage's RunEvent / Dataset / Job model is the prior-art target; the substrate's version maps assertions to RunEvents and entities to Datasets, with the chunk_id binding providing finer granularity than OpenLineage's per-dataset model natively supports.

### 11.7 Boot continuity surface for handoff prompts

**Resolved by Appendix C — see C.5.** The boot card's `continuity.tail` carries only `prior_session_id` + `continues_edge_id`; handoff prose is referenced via `handoff_uri` but never inlined. Inlining handoff prose would launder its orientation-grade status per §12.1. This closes the open issue — the bootstrap-only principle in §12.10 and Appendix C.5 is the resolution.

Handoff prompts are captured by `session_close(handoff_prompt=...)` but are NOT auto-surfaced at boot (per assertion 8384). The user copy-pastes them. v2 may surface specific handoff prompts on user request via the boot manifest's existing `continuity` section (`GET /boot-continuity`) — exposed but not defaulted. The default remains: boot is lean; handoffs are explicit-recall artifacts.

### 11.8 Phase-closure-spine gap as §8 finding kind

§9.2 names the phase-closure-spine gap (phase closed without successor). v2 may add `phase_closure_spine_gap` as a tenth §8 finding kind, surfaced by a graph traversal over `plan_phase:` and `todo:` entities at session-close time.

### 11.9 Tooling: structural-field validation at write time

§5.4 ships advisory `validation_warning`s for §5.1 R3 and partial R4. v2 may promote selected gates to blocking once false-positive rates are characterized — likely R3 (URI presence) first, since the structural form is unambiguous.

### 11.10 Cross-jurisdictional / cross-domain authority resolution

The brief-spec §10.8 names jurisdiction-as-attribute on `case-law:` (federal circuit vs state court, jurisdictional weight). The universal spec analog: for any domain instantiation, the authority entity carries a `jurisdiction` or `domain_scope` attribute and the resolver respects scope mismatches. v2 will specify the resolver contract for cross-scope citations.

---



### 11.11 `prospective_summary` regeneration triggers

The §7.3 field-preservation contract specifies that `prospective_summary` auto-regenerates on supersede when the new claim differs from the predecessor. v2 will formalize WHEN regeneration is triggered for non-supersede write events: e.g., when an assertion's `evidence` is updated but the claim text is unchanged; when a new corroborating reasoning edge lands; when the entity's surrounding assertion set grows enough that the forward-relevance projection meaningfully shifts. Currently regeneration happens only at supersede; v2 may add a `regenerate_prospective_summary` operation for explicit triggers.

### 11.12 `events_json` schema versioning

The `events_json` schema currently serializes as a JSON array of `{event, consequence, temporal}` triples (per §4.7.2). v2 will formalize the schema with a version field and enumerate additional optional fields: `actors` (entity IDs of the agents/principals involved); `preconditions` (causal antecedents); `confidence_per_event` (granular confidence on each triple when the overall assertion's confidence is aggregate). Schema versioning enables backward-compatible evolution without invalidating the existing event corpus.


## § 12. Consumer obligations

The read-time discipline. §5 governs claim entry; §12 governs claim citation. Together they close the loop: a correctly-graded claim cannot be silently re-graded by a consumer that paraphrases the orientation surface or strips the evidence envelope.

### 12.0 Canonical failure anchor

Session `claude-web-2026-05-15-0310` (5 AM boot, web Claude). Three simultaneous failures across substrate write, substrate read, and consumer reasoning:

1. **Scrubbed claim survived as `summary_row`.** Assertion 9004 (`confidence: confirmed`, entrenchment 0.92) recorded that the "phishing actor had insider-level knowledge of his 5 AM Uber selfie verification" formulation had been inference-scrubbed from the security-incident report on 2026-05-11. The scrubbed text nevertheless persisted in `case:uber-driver-harassment-2026.summary_row`, which the boot card inlined and the consumer then quoted to Kaywan as fact.

2. **Inference-derived assertion cited as reassurance.** Assertion 9205 ("The Wallet information has been unedited") was retrieved and cited to reassure Kaywan that direct-deposit routing was intact. Its `derivation_type` was `inference`, not `direct_observation`. The grade was visible in the payload. The consumer ignored it.

3. **Model priors substituted for substrate consultation.** Consumer recommended Kaywan "call your bank to flag the May 18 ACH origination before it processes." ACH mechanics make this impossible — a receiving bank cannot see an originator's transfer before it arrives. No substrate citation; model-priors hallucination presented as procedural advice.

| Layer | What failed | §12 control |
|---|---|---|
| Substrate-derived field | Scrubbed claim survived in `summary_row` | §12.6 (links §3.5) |
| Substrate read | Inference grade not surfaced at cite time | §12.2, §12.3 |
| Consumer reasoning | Domain advice given without authority gate | §12.8 |

All three are spec-layer gaps. A higher-capability model running the same boot against the same substrate would have hit the same surface and fallen into the same trap. §12 is the spec-layer fix; model capability is not.

### 12.1 Field-grade taxonomy

Every field on a substrate entity is one of:

- **`evidence-grade`** — usable as the basis for a user-facing factual claim. Examples: assertion `claim` with full envelope; `evidence_uris` resolving to primary source; verbatim quotation chunks.
- **`orientation-grade`** — usable for routing, navigation, and intent recognition only. Never the basis for a user-facing factual claim. Examples: `summary_row`, entity `name`, journal prose, handoff prose, boot card descriptive prose, RAG snippet text, compressed open-item sentences.
- **`structural`** — pure metadata. IDs, timestamps, counts, edge types, `workflow_state`. Safe to cite as system state ("entity has 4 active assertions") but never as substantive case fact.

Every entity-type schema declares the grade of each field. Consumers MUST check the grade before citing. Citing an `orientation-grade` field as substantive fact is a spec violation.

§12.1 is the read-time companion to §3.1.1's mixed-grade artifact handling. §3.1.1 defines how artifacts declare per-row grading via inline anchors at write; §12.1 defines what consumers do with the grade at read. Both surfaces use the same three-value taxonomy.

### 12.2 Read-time evidence envelope

Every assertion returned by substrate read tools carries the envelope:

```
assertion_id
claim
confidence                       # confirmed / believed / suspected / hypothesized
derivation_type                  # per §3.1 taxonomy
observed_at
valid_from / valid_until
superseded_by                    # null or assertion_id
conflicts_with                   # list or null
is_current_controlling           # bool
can_support_deliverable_claim    # yes / qualified / no
permitted_language_class         # per §12.3
evidence_uris
```

The two consumer-facing computed fields:

- **`can_support_deliverable_claim`** — substrate's read-time judgment on whether the assertion is fit to ground a user-facing factual claim. Missing or null defaults to `qualified`, never to `yes`.
- **`permitted_language_class`** — substrate's classification of the evidence state. Consumers translate via §12.3.

Consumers MUST honor these fields. Default substrate read returns only current-controlling assertions; historical and superseded require explicit `include_history=true`.

### 12.3 Permitted-language mapping

Hard mapping from evidence state to permitted consumer language. The substrate computes `permitted_language_class` at read time and consumers translate via this table:

| Evidence state | Permitted language |
|---|---|
| `confirmed` + non-inference derivation + current + unconflicted + `evidence_uris` present | "X occurred" / "X is the case" |
| `confirmed` + `user_statement` derivation | "User stated X" — NOT "X happened" |
| `confirmed` + `quotation` derivation | "The source says X" / "The document records X" |
| `confirmed` + `inference` derivation | "One inference is X" / "This is consistent with X" — flagged as inference |
| `believed` (any derivation) | "Believed: X" / "It appears that X" |
| `suspected` | "Suspected: X" |
| `hypothesized` | "Hypothesis: X" |
| `compression` / orientation-grade summary | Insufficient for substantive claim. Use only to route to evidence. |
| `superseded` | Do not present as current. May reference as history when explicitly relevant. |
| `conflicted` | State the conflict or withhold. Never silently pick one side. |

The 5 AM cascade's second failure — citing assertion 9205 (`derivation_type: inference`) as reassurance — is the canonical violation of this table's `confirmed + inference` row. The grade was visible; the consumer applied the wrong language class.

### 12.4 User-statement-vs-event proposition distinction

The single most-common laundering vector. A `confirmed` `user_statement` confirms only that the user *said something*, not that the underlying event occurred. The substrate records the proposition correctly:

- ✅ `confirmed`: user said "X happened"
- ❌ `confirmed`: X happened

The auditor-validatability gate (§5) extends to enforce this distinction at write. Consumers reading a `user_statement` assertion MUST apply the §12.3 "User stated X" language class, never the unqualified "X occurred" class.

### 12.5 Per-response claim ledger

Before any substantive consumer response, the consumer constructs (internally; not user-visible by default) a ledger of every material claim it intends to assert:

```
claim_text → supporting_assertion_id → evidence_state → permitted_language → decision
```

Any claim with `can_support_deliverable_claim != yes` either gets reframed under §12.3 or is dropped from the response. The ledger is the consumer-side parallel to §5.3's pre-write checklist and to the Boot Execution Discipline write-ledger.

### 12.6 Dependency tracking on derived fields

The consumer-side counterpart to §3.5. When retrieving content from an orientation-grade derived field (`summary_row`, journal prose, search snippet, boot-card prose), the consumer MUST check the field's `freshness` status before quoting. A `freshness: stale` field is not quotable as current state.

### 12.7 Search and RAG grade hygiene

Embedding indexes and full-text search distinguish evidence-grade from orientation-grade content. Snippets in retrieval responses carry field-grade in the response payload. A consumer that mixes grades without honoring the markers is in violation.

### 12.8 Domain authority gate

For claims outside the Cortex evidence corpus — legal procedure, financial mechanics, medical, safety, security operations, regulatory mechanics — the consumer MUST identify the relevant skill or authoritative reference and either cite it or explicitly state the claim is from model priors and unverified. Every domain-bearing claim declares its source class: `substrate-cited`, `skill-cited`, or `unverified-priors`.

### 12.9 Runtime gating handshake — `boot_discipline_ack`

The consumer's first substantive response in a session is gated behind a tool-level acknowledgment confirming §12.1 field-grade, §12.2 evidence envelope, §12.3 permitted language, §12.5 claim ledger, §12.6 dependency tracking, and §12.8 domain authority. Prompt-only instruction has been demonstrated to fail (§12.0 anchor). The fix is a runtime gate, not stricter prose.

### 12.10 Boot as reference consumer

The boot is the first and most-trusted consumer of substrate data. **Bootstrap-only principle.** The boot bootstrap contains discipline + structural state + manifest. No orientation-grade prose. No `summary_row` content. No descriptive case names that encode case theory. No journal summary text. All such content is retrievable on demand under §12.6 + §12.7. See Appendix C for the complete boot-time provenance surface schema.

### 12.11 Anti-patterns

- *Quoting orientation-grade content as evidence.* Always check field-grade before quoting.
- *Treating `confirmed + inference` as `confirmed + direct_observation`.* The 5 AM cascade's second failure.
- *Substituting model priors for domain authority.* The 5 AM cascade's third failure.
- *Skipping the claim ledger.* Without §12.5, no observable record of which claims were grounded in evidence.
- *Treating `boot_discipline_ack` as performative.* Acknowledging obligations and then violating them is a worse failure than declining to acknowledge.

### 12.12 Origin and open-questions disposition

Drafted from assertion 9761. Five open questions from 9761 resolved: (1) scope split across §3.1.1 + §12; (2) enforcement layer → §13; (3) migration path → §3.5 + §14; (4) field-grade declaration → §3.1.1 + §3.5; (5) skill-router scope → §8.2.12.


### 12.13 Output citation grammar and enforcement

When a consumer's response makes a material claim grounded in a
substrate assertion (per §12.5 ledger), the response MUST emit an
inline citation anchor adjacent to the claim. Canonical grammar:

    [assertion:NNNN]   where NNNN is the integer assertion_id.

Examples (single source):
    The notice was mailed 2025-08-27 [assertion:9847].
    Beneficial ownership transferred 2025-05-02 [assertion:9612].

**Multiple supporting assertions.** A claim backed by multiple
assertions emits one bracket per supporting assertion, adjacent and
unspaced:

    The notice was timely mailed [assertion:9847][assertion:9851][assertion:9852].

Each bracket is an independent citation and registers as a separate
§12.5 ledger row (sharing `claim_text` but with distinct
`supporting_assertion_id`). The validator parses each `[assertion:NNNN]`
occurrence independently; adjacency carries no additional semantic.

Authors SHOULD prefer atomic decomposition (per PaperTrail's
atomic-claim principle, Appendix A) when a fused claim can be split
without semantic loss. Multi-bracket citation is the fallback for
genuinely fused claims. Citation density ≥ N=8 triggers §8.2 finding 14
`output_citation_high_cardinality` as an authoring-smell warning. For
genuinely cumulative claims (e.g., "total fees paid: $X" backed by
every invoice), authors SHOULD use a set-aggregating assertion (§3.1
`aggregation` derivation type) with a single inline citation to the
aggregate.

**Scope (when required):**

1. Required for structural-grade claims (§12.1) in human-consumed
   output (legal-brief domain, derived-artifact authoring, dispatch
   outputs that feed downstream agents).
2. Required for belief-grade claims (§12.1) when the claim is
   load-bearing for a deliverable. Grade/derivation is resolved from
   the §12.2 envelope by lookup; the inline anchor does not encode
   grade.
3. NOT required for orientation-grade prose (§12.1) or for non-load-
   bearing conversational text.

**Read-time enforcement (Phase 1.0 / Phase 1.5):**

The output validator parses `\[assertion:(\d+)\]` occurrences,
resolves each to an assertion via cortex.assertion_get, and emits §8
finding kinds when mismatches arise:

  - Finding 13 (`output_citation_missing_assertion`): citation does
    not resolve, OR resolves to a superseded/retired assertion, OR
    absent on a load-bearing ledger entry. Phase 1.0.
  - Finding 5 ext (`verbatim_check_failed` output-side): quoted text
    does not match chunk_id-bound assertion claim text after §4.3
    normalization. Phase 1.0.
  - Finding 14 (`output_citation_high_cardinality`): claim has ≥ 8
    supporting_assertion_id rows. Phase 1.0.
  - Finding 15 (`grade_laundering_in_output`): structural-grade output
    cites belief-grade / user_statement assertion without §12.3
    framing. Phase 1.0.
  - Finding 16 (`temporal_qualification_omitted`): date claim cites
    assertion with valid_from but output omits date binding. Phase 1.0.
  - Finding 17 (`bibliography_orphan`): bibliography ↔ body cross-ref
    mismatch in derived-artifact authoring. Phase 1.0.
  - Finding 18 (`output_citation_semantic_mismatch`): citation resolves
    correctly but §14.2(b) semantic-similarity between claim text and
    cited assertion's claim text falls below threshold. Phase 1.5.

**Brief-domain `review_required` signal.** When the validator runs
against a brief-domain artifact (detected by domain tag in dispatch
metadata), it emits a `review_required: True` signal to the
dispatcher. The dispatcher MUST route to a §10.1-compliant
adversarial review pass before publish/file. This is the bridge
between automated validation (this section) and the deterministic
human/independent-model backstop (§10.1).

**Note on what this grammar does NOT enforce deterministically:**

The form `[assertion:NNNN]` validates that a citation is present and
resolves to an active assertion. It does not deterministically validate
that the cited assertion semantically backs the specific claim it
accompanies. Wrong-assertion citation (citing assertion X to back
claim Y where X does not support Y) is detected probabilistically by
finding 18 (semantic-similarity check) AND deterministically by the
§10.1 mandatory adversarial review pass for brief-domain artifacts.

Per §13.7 and §14.4, the residual class after finding 18 + §10.1
review is documented, not eliminated: doctrinal-reasoning errors
(theory reframes, overclaims as legal doctrine — e.g., BOE-19-P v5
Findings 6, 10, R2, R3) are out of citation-validation scope entirely
and remain dependent on the §10.1 review pass's substantive
proposition-fit / remedy-fit criteria.

## § 13. Enforcement layer split

Defense-in-depth for supersession and freshness. Three layers, all required:

| Layer | When it fires | What it catches | What it misses |
|---|---|---|---|
| **13.3 Substrate-primary** | On assertion write | Same-`(entity_id, predicate_form)` supersessions; retroactive once §14.1 completes | Different-`predicate_form` same-fact pairs (the 9020/9023 class — caught by §13.4 + §14.2(b) semantic similarity) |
| **13.4 Audit-backstop** | session_close + §14.2 retroactive pass | What §13.3 missed: predicate_form-NULL pairs, semantic-overlap pairs crossing `predicate_form` boundaries, substrate-layer write-window races | Cross-session supersessions that span the audit window |
| **13.5 Reader-defense-in-depth** | At read / quote time | Whatever §13.3 + §13.4 missed; protects the moment of consumer citation | Pure model-discipline layer — fails when discipline is implicit |

### 13.1 Canonical failure anchor — 2026-05-14 Uber doc-index supersedence failure

Peer to §5.6's *Mata v. Avianca* anchor. In session `claude-web-2026-05-14-1301`, the agent authored `legal/uber/document-index.md`. The "Authoritative source (text)" row for the Uber security-incident report quoted assertion 9020 three days after assertion 9023 had established `…reconstructed-for-docx.md` as the authoritative source. Both assertions had `superseded_by: NULL` and `predicate_form: NULL`. The substrate had no structural marker indicating supersession; the reader selected on filename heuristics rather than temporal ordering. Kaywan caught it by human-eye scan.

The incident is direct evidence that **only the reader-side layer was in effect, and the reader-side layer failed** because the discipline was implicit — not enforced by tooling. §13 fixes this by specifying all three layers, with the reader-side layer as defense-in-depth rather than primary.

### 13.2 Why all three layers

- *Substrate-primary alone*: forward-protected from the day it ships, but the historical corpus stays exposed until §14.1 backfills `predicate_form`. Even after §14.1, literal `predicate_form` equality misses the 9020/9023-class false negative.
- *Audit-backstop alone*: catches the literal-miss and semantic classes via §14.2(b), but only at session close.
- *Reader-defense-in-depth alone*: 2026-05-14 demonstrated the failure mode.

### 13.3 Substrate-primary layer

**Trigger:** every `cortex(tool='assert', ...)` call. On assertion write, query for same-`(entity_id, predicate_form)` active rows. If matches exist, fire auto-supersede (when new assertion is stronger), propose-supersede (ambiguous comparison), or block (would silently contradict higher-confidence assertion). **Retroactive protection gated on §14.1 predicate_form backfill.**

### 13.4 Audit-backstop layer

**Trigger:** session_close + §14.2 retroactive passes. Walk all session-written assertions; run literal-match + semantic-similarity comparison (§14.2(b)); pipe into two-pass dispatch (§14.2(c)); auto-apply high-confidence supersessions; queue mid-confidence for review. Every action lands in `notes/system/threads/v1.3-supersedence-backfill-ledger.md`.

### 13.5 Reader-defense-in-depth layer

**Trigger:** every consumer read that quotes assertion content. Before quoting assertion A: check `A.superseded_by`; query newer same-predicate active rows; emit freshness warning if successors exist. **Why still required:** with §13.3 + §13.4 in place, the substrate carries supersession state structurally — but the reader still has to *check* the state before quoting. §13.5 is the moment-of-citation enforcement that turns substrate state into consumer behavior. Skill-router scope extends to derived-artifact-authoring per §8.2.12.

### 13.6 Sequencing and dependencies

§13.3 substrate-primary requires §14.1 predicate_form backfill for retroactive corpus coverage. §13.4 audit-backstop requires §14.2(a) literal-match + §14.2(b) semantic-similarity passes; the semantic layer is load-bearing for §13.4, not optional (the 9020/9023 canonical case is caught by §14.2(b) only). §13.5 reader-defense-in-depth requires no prerequisite passes.

### 13.7 Honesty contract

The three-layer combination reduces supersession failure to a documented residual rate per §14.4. The spec does not claim 100% detection — naming the residual class is the minimum honest position.


## § 14. Implementation prerequisites

The supersession discipline of §7, the dependency tracking of §3.5, and the enforcement layer split of §13 protect **forward** by construction: any assertion written under v1.3 carries the structural fields (predicate_form, derives_from, anchors) that the discipline operates on. Retroactive protection — applying the same discipline to assertions already in the corpus — is GATED on two prerequisite passes over the existing graph.

**Without these prerequisites, v1.3 documents protection it does not provide for the existing corpus.** This section makes the gating explicit and tracks completion as a primary-source anchor.

### 14.1 Predicate_form backfill

**Problem.** The supersedence-detection contract in §13 keys on `(entity_id, predicate_form)` pairs. Any assertion with `predicate_form IS NULL` is invisible to detection. The 2026-05-14 Uber doc-index failure is the canonical case: both assertions 9020 and 9023 had NULL `predicate_form`.

**Sign-off.** Backfill is complete when `SELECT COUNT(*) FROM assertions WHERE superseded_by IS NULL AND predicate_form IS NULL AND entity_id NOT LIKE 'test:%'` returns 0.

**Status (2026-05-17):** ✅ COMPLETE — thread 1013 verified 139/139 rows backfilled, 0 factual errors. The literal-match layer (§13.3) is now retroactively active over the full corpus.

### 14.2 Pairwise supersedence-candidate detection

**Problem.** Even with predicate_form fully backfilled, the corpus contains assertion pairs where a newer assertion has implicitly superseded an older one but no `supersede()` call was ever made. Two detection classes are required: literal predicate-match (§14.2(a)) and semantic-similarity backstop (§14.2(b)).

#### 14.2(a) Literal predicate-match layer

SQL pass: group by `(entity_id, predicate_form)` filtered to active non-test assertions with non-NULL predicate_form; emit groups with count ≥2. Each group emits C(n,2) candidate pairs; pass to two-pass dispatch (§14.2(c)).

**Known false-negative class.** Pairs where the same load-bearing fact gets different predicate_form judgments (canonical case: assertions 9020 vs 9023 on `document:uber-security-incident-report-2026-05-07`). The semantic layer below exists specifically to catch this class.

**Status (2026-05-17):** ✅ COMPLETE — §14.2(a) + (b) passes ran via thread 1013 / §14.2 arc; §14.2(c) two-pass dispatch applied; supersession chain closed. `normalize_predicate_domain()` implemented and write-time wired (assertions 10251 + 10335).

#### 14.2(b) Semantic-similarity backstop layer

For each entity with ≥2 non-superseded assertions, compute pairwise cosine similarity over claim text for pairs not already captured by §14.2(a). Pairs with cosine_similarity ≥ T (starting threshold T = 0.75; tunable) enter the candidate set. Pass to §14.2(c).

#### 14.2(c) Two-pass dispatch over candidates from (a) and (b)

1. **First pass:** `gpt-5.4-mini` reviews each pair, emits `{verdict, confidence, reasoning, source_layer}`. 2. **Second pass:** `gpt-5.4` (full) reviews uncertain or low-confidence pairs. 3. Auto-apply tier: confidence ≥0.85 → auto-supersede; 0.5–0.85 → human review queue; <0.5 or non-supersedes → decision-made record.

#### 14.2(d) Path B programmatic re-run (v4 §10.4 acceptance gate)

Run `normalize_predicate_domain()` from `libs/predicate_form/` over the active corpus; generate cluster set keyed by `(entity_id, canonical_form)` with cardinality ≥2; compare to baseline.

**Status (2026-05-17):** ✅ COMPLETE — re-run via thread 1016 returned SUPERSET result (158 clusters across full corpus vs 34-cluster baseline over 45 entities). All 34 baseline clusters dissolved legitimately: §14.2(a) pairs applied 2026-05-16 superseded the cluster members, collapsing each to a single canonical. C6/C7 (mortgage payments) expired via temporal `valid_until` bounds. Python function correctly identifies current-corpus redundancy. Criterion (a) PASSES.

### 14.3 Sequencing and dependency

§14.1 MUST complete before §14.2(a). §14.2(b) does NOT structurally depend on §14.1 but practically sequences after for clean ledger ordering. The canonical **9020/9023** pair is caught by §14.2(b), not §14.2(a) — the semantic layer is load-bearing.

### 14.4 Honesty contract

Retroactive-protection language in §3.5 and §13 is **effective only after §14.1 AND §14.2(a) AND §14.2(b) complete**. Both are now complete as of 2026-05-17. Residual false-negative class remains documented: pairs that escape literal-match detection, semantic-similarity detection at the converged threshold, AND the reader-side scan. The spec does not claim 100% detection.

`document:cortex-provenance-substrate-spec-v1.3` ships with `status: draft` pending formal Cortex v3.0 close stamp; consumer-facing retroactive guarantees in §3.5 and §13 are now effective over the full corpus.

## Appendix A — Prior art

The spec extends and adapts the following prior-art corpus. Each entry names the standard or architecture, what it contributes to the substrate, and what this spec adds.

**Standards:**

- `temporal-provenance/w3c-prov-dm.html`, `w3c-prov-o.html`, `w3c-prov-n.html`, `w3c-prov-constraints.html` — **W3C PROV** is the prior-art standard for provenance. PROV provides Entity / Activity / Agent primitives; this spec adapts to a knowledge-graph-native setting with `derivation_type` co-requirements and `is_independent`-gated cross-source verification. The substrate's §1 primitives align with PROV's data model; the §7 supersession discipline aligns with PROV's invalidation semantics; this spec's contributions are the §5 auditor-validatability gate, the §6 family/version independence gate, and the §4.7 forward-looking primitives, which PROV does not specify.

- `temporal-provenance/openlineage-object-model.html` — **OpenLineage** is the lineage-tracking model for data pipelines. This spec's §4.6 `lineage` attribute serializes the same shape (RunEvent / Dataset / Job), with the §11.6 v2 work aligning the serialization formally.

**Architectures:**

- `temporal-provenance/papertrail-claim-evidence-provenance.pdf` — **PaperTrail (Martin-Boyle et al., CHI '26)** is the closest sibling architecture. PaperTrail encodes claim-evidence relationships at atomic / faithful / decontextualized / verifiable / declarative claim granularity (PaperTrail §3.1) via span annotation with NLTK punkt tokenizer + programmatic matching. This spec extends PaperTrail's claim-decomposition with:
  - The §6 cross-model independence gate (PaperTrail evidence is sourced; this spec gates the agent generating the evidence).
  - The §5 auditor-validatability requirement (PaperTrail does not specify a verbatim-embedding contract; this spec does).
  - The §7 AGM-compliant supersession (PaperTrail is a static record; this spec is a temporally-evolving substrate).
  - The §4.7 forward-looking primitives (PaperTrail has no future-relevance projection; this spec's `prospective_summary` and `events_json` make the substrate a learning corpus).

- `temporal-provenance/trove-fine-grained-text-provenance.pdf` — **TROVE** is the text-provenance precursor at sub-document granularity. TROVE's derivation taxonomy (`quotation` / `compression` / `inference` / `other`) is the prior art for Cortex's `derivation_type` field (per `service:cortex` assertion 101). This spec's §3 taxonomy extends TROVE with the observation types (`direct_observation`, `agent_observation`, `user_statement`) required to handle agent-tool-mediated evidence and direct user input, plus `commitment` for performative claims and `stated`/`other` as escape hatches.

- `belief-consistency/graphcheck-kg-powered-fact-checking.pdf` — **GraphCheck** is the KG-based fact-checking baseline. This spec extends with the §6 cross-model independence gate and the §5 auditor-validatability gate; GraphCheck's fact-checking is post-hoc, this spec's discipline is write-time.

**Forward-looking provenance (new in this spec, derived from):**

- **Kumiho — graph-native cognitive memory with prospective indexing.** Kumiho's LoCoMo-Plus benchmark (Kumiho paper §15.3, agent-bus thread 435 directive recorded as `service:cortex` assertion 1516) established that prospective indexing eliminates the >6-month accuracy cliff in similarity-only retrieval, with accuracy from 61.6% baseline to 93.3% with prospective_summary + event extraction. This spec's §4.7 makes `prospective_summary` and `events_json` first-class assertion fields with write-time generation contract and §8 gap-detection coverage. Cortex independently converged on the graph-native memory framing; Kumiho retroactively validated (per `document:cortex-v3-spec` assertion 1675).

- **`document:cortex-v3-spec`** — the Cortex v3.0 architecture spec deployed in migration 019 (2026-04-05) adding the four forward-looking columns (`prospective_summary`, `events_json`, `artifact_uri`, `artifact_storage`) to the assertion row. v3 is the read-model layer; this spec is the write-time discipline layer that treats v3's columns as load-bearing substrate primitives rather than optional metadata.

- **`artifact:epistemic-substrate-paper-draft`** (drafted in `transcript:web-2026-05-13-0438`) — the three-pillar substrate framing (Memory + Provenance + Consensus) that made forward-looking provenance load-bearing for the public-artifact narrative. §3.1 Memory pillar: *"This record has two functions: audit trail for the human operator, and feedback corpus for future agent runs."* The forward-looking primitives are the structural implementation of the second function.

**Belief-revision theory:**

- **AGM belief revision (Alchourrón–Gärdenfors–Makinson 1985)** — the foundational theory for expansion / contraction / revision of belief sets. The substrate's `service:cortex` assertion 1854 records the AGM-compliance commitment (Kaywan directive 2026-04-08). This spec's §7 operationalizes AGM via atomic supersede + field-preservation contract.

**Lineage and belief-revision lineages:**

Several adjacent research traditions specify lineage, annotation, or belief-revision substrates this spec relates to without subsuming. Each entry is descriptive related work — useful for orienting Cortex's choices within a wider literature, not a normative authority for any single Cortex mechanism.

- **Provenance semirings (Green, Karvounarakis, Tannen, PODS 2007 and successors)** — an annotation-algebra approach to query-time provenance over relational data: semiring-valued annotations propagate through query evaluation to compute fine-grained "how" and "why" provenance for each derived tuple. The substrate's `derivation_type` field (§3.1) is a coarser, categorical analog at the assertion layer; where semirings carry composable algebraic structure ranging over arbitrary lineages, `derivation_type` partitions evidence paths into a fixed taxonomy whose co-requirements are enforced at write time. The trade-off is intentional: Cortex prioritizes auditor-readability of a discrete categorical signal over algebraic compositionality. The two approaches are complementary at different layers of the stack.

- **Datalog-style data lineage and truth maintenance systems** — Datalog's deductive-database semantics produce derivation trees that trace each derived fact back to its base-fact dependencies. Doyle-style truth maintenance systems (TMS, JTMS, ATMS) layer doxastic justifications on top, supporting non-monotonic retraction when assumptions are withdrawn. The substrate's confidence ladder (§2) and supersession discipline (§7) share the TMS commitment to traceable belief change: every `confirmed` assertion records the evidence path that produced it, and every supersede preserves the predecessor row so the chain remains queryable. Cortex differs in granularity (assertion-level rather than literal-level) and in scope (mixed-mode evidence including LLM-mediated tool outputs, not just deductive consequences over a closed base).

- **CRDT-based knowledge graphs (Yjs, Automerge as exemplars)** — conflict-free replicated data type frameworks produce knowledge-graph variants where concurrent writes from distributed agents merge deterministically without central coordination, via operation-based or state-based CRDT semantics over the graph's deltas. The substrate currently centralizes write coordination through the cortex write surface and relies on the §7 AGM-style atomic supersede to handle revision. The CRDT-KG tradition is the architectural alternative for a multi-agent Cortex deployment where the coordination point becomes a bottleneck; convergence guarantees would shift from "atomic supersede succeeds-or-fails together" to "all replicas converge to the same revision graph eventually." v2 may revisit the trade-off if multi-region or fully-peer-to-peer deployment surfaces the bottleneck.

- **Bitemporal property graphs (Rost et al. 2021)** — extend the property graph model with two independent time dimensions per vertex, edge, and property: valid-time (the application-world period during which the fact holds in the real world) and transaction-time (the database-world period during which the fact was recorded in the store). Each is represented as a time-period τ over the relevant time domain (`Ω_val`, `Ω_tx`). The substrate's temporal field set — `observed_at` for the transaction-time anchor, `valid_from` / `valid_until` for the valid-time window, plus the supersede chain as the implicit transaction history — is the assertion-layer analog of TPGM⁺'s edge-and-property bitemporal attributes (§4.5, §7.3). Cortex maintains both dimensions per assertion row rather than per property within a row; the trade-off favors per-claim audit granularity over per-attribute storage compactness.

- **Belief-revision lineages — AGM (Alchourrón–Gärdenfors–Makinson 1985) and Hansson belief-base operations (1999)** — AGM specifies expansion / contraction / revision as set-theoretic operations over closed belief sets satisfying the basic postulates; Hansson's belief-base framework relocates the same operations to finite, non-closed belief bases, producing operational change histories that can be traced row by row. The substrate's §7 atomic supersede operationalizes the Hansson-base reading: revisions land as new rows; the predecessor's `superseded_by` field anchors the lineage; the chain remains queryable for time-aware reasoning. Cortex extends the AGM lineage tradition with two additions classical AGM does not specify but the multi-agent LLM setting requires: explicit confidence labels per row (§2), and the §6 cross-model independence gate on verifiers of `confirmed` claims. Kumiho (cited above under Forward-looking provenance) sits in this same lineage tradition; it is the closest sibling architecture proving AGM-postulate satisfaction on a graph-native belief base.

**Model-confidence calibration:**

- **Kadavath et al., "Language Models (Mostly) Know What They Know"** — establishes that LLMs have probability-calibration signal accessible via self-evaluation prompts. This spec's confidence ladder is informed by but not derived from this signal: §2's ladder is evidence-bound (what the agent can show an auditor), not calibration-bound (what the agent believes about its own probability).
- **R-tuning** — instruction-tuning approach for refusal-on-uncertainty. The substrate's §2.3 downgrade semantics serve a similar role at the assertion layer: write at a lower confidence is the substrate analog of refusal-on-uncertainty.

**Legal-AI failure modes the substrate renders structurally inaccessible:**

- `legal-reasoning/mata-v-avianca-findlaw-full-text.html` — the original ChatGPT-hallucinated-citations sanction (S.D.N.Y. 2023). Citation tokens that don't resolve to graph entities become §8 `missing_backing_entity` findings at severity `high`; publish gate refuses.
- `legal-reasoning/park-v-kim-second-circuit-ai-hallucination-sanction.pdf` — Second Circuit sanction extending Mata to appellate practice. Citation resolves but was never fact-checked → §8 `unverified_entity` or `unverified_claim` finding.
- `legal-reasoning/large-legal-fictions-legal-hallucinations.pdf` — Stanford empirical study on legal hallucination prevalence. Motivates the structural impossibility framing (§8.7).
- `legal-reasoning/hallucination-free-rag-legal-tools-assessment.pdf` — Stanford assessment of commercial RAG-based legal tools. Establishes that RAG alone is insufficient; the §5+§6 gates are what close the gap.
- `legal-reasoning/aba-formal-opinion-512-generative-ai.pdf` — ABA Formal Opinion 512 on generative AI and Model Rule 1.1 (competence). This spec is a Rule 1.1-enabling architecture.

**Prior-art search session:**

The above corpus was traced via `rag(scope=research)` in session `web-2026-05-12-2204` during the brief-spec's reconciliation pass. The same corpus grounds this universal spec, with the forward-looking provenance additions traced in session `claude-web-2026-05-13-1806` from `document:cortex-v3-spec`, `service:cortex` assertion 1516, and `artifact:epistemic-substrate-paper-draft`.

---

## Appendix B — Reviewer attribution and independence disclosure

*[Reviewer attribution to be filled in after the SuperHeavy review consult and any additional reviewer dispatches. Template follows the brief-spec Appendix B format: reviewer model, dispatch shape, independence classification (independent / NOT independent per §6 family/version gate), confidence profile across review questions, and notable empirical findings beyond the consult questions.]*

Pending entries:
- `xai/grok-superheavy` — SuperHeavy review of this spec + load-bearing PDFs per `agent_skill:grok-web-dispatch`. Review questions covering principle generalization, prior-art completeness, failure-mode coverage, workflow-patterns scope, family/version granularity, supersession field-preservation contract.
- Additional reviewers TBD per the brief-spec's three-reviewer pattern (likely `openai/gpt-5.5` + `google/gemini-3-pro` for independent panel; same-model cursor reviewer disclosed-as-non-independent if included).

Reviewer-independence classification at write time will use §6.2's `family/version` normalization. Same-model reviewers will be explicitly disclosed as non-independent per §6.5.

---


## Appendix C — Boot-time provenance surface

The boot card is the most-trusted consumer of substrate data and the first surface every session reads. §12.10 specifies the principle; Appendix C specifies the contract.

### C.1 The grade-and-manifest invariant

Every byte the boot inlines is either **structural** or **discipline**. No orientation-grade prose. Substrate content lives behind a section manifest with retrieval hints; orientation-grade prose is fetched on demand under §12.6 + §12.7.

The grade-and-manifest invariant restated as a single rule: **if a reader could quote a boot-card field directly as a substantive factual claim about the world, the field is spec-violating.**

### C.2 Bootstrap content schema (exhaustive)

The boot card MUST contain exactly: `session` (session_id, utc_now, agent, family, platform, role), `continuity.tail` (prior_session_id + continues_edge_id only; no prose summary), `structural_state` (deadlines with source_assertion_id, bus.unread_count + thread_ids, staging.pending_count, review_queue counts, recent_mentions as IDs + structural delta only), `critical_alerts` (all counts; never inlined prose), `discipline` (boot_precedence_ladder inline, consumer_obligations_ref pointer, write_verify_report_loop inline, boot_discipline_ack_required), `section_manifest` (opaque; no inlined content; hint per section), `skill_index` ({skill_id, tags, priority, version_hash} per entry; no description prose).

### C.3 What the boot card MUST NOT contain

Spec-violating boot content (exhaustive, enforceable): `entity.summary_row` content inlined; `entity.description` content inlined; last-session journal prose inlined; open-item compressed sentences inlined; recent-mention descriptive prose; plan-phase prose descriptions; todo descriptions beyond `{id, tags, priority, workflow_state}`; descriptive entity slugs that encode case theory; inline rendering of bus turn bodies; inline rendering of staging review-queue rows.

### C.4 Section manifest — retrieval API contract

Each section in `section_manifest` carries a `hint` string naming the retrieval API call the agent executes to fetch the section. Every retrieval endpoint: (1) returns evidence-envelope-bearing content per §12.2; (2) honors §13.5 freshness pre-application; (3) carries §12.1 field-grade tagging on every response value.

### C.5 The continuity tail — no inlined prose

The boot card's `continuity.tail` carries only `prior_session_id` and `continues_edge_id`. Handoff prose, if any, is referenced via `handoff_uri` but never inlined.

**This is the §11.7 (open issue v2) resolution.** Handoff prose is an orientation-grade field by §12.1; inlining it would launder the grade and break §12.10's bootstrap-only principle. The handoff prose remains accessible via explicit-fetch; the default boot doesn't surface it.

### C.6 Critical alerts surface — counts, not content

The `critical_alerts` block reports every counter even when zero. A non-zero counter triggers a default fetch obligation on the consumer's first substantive response.

### C.7 Skill discovery — tool-router, not boot inlining

The `skill_index` carries `{skill_id, tags, priority, version_hash}` per entry, never description prose. Trigger resolution is the tool-router's job. The 5 AM cascade demonstrated that model-side scanning of long descriptive trigger lists is unreliable.

### C.8 Operational context file — the durable read-time surface

The boot returns a small inline card (~5-10 KB) AND writes a larger operational-context file to `notes/system/shared/operational-context-<agent>-<role>.md` (~20-30 KB). Both honor the grade-and-manifest invariant.

### C.9 Canonical failure anchor — claude-web-2026-05-15-0310 (5 AM cascade)

Peer to §5.6's *Mata v. Avianca* anchor and §13.1's 2026-05-14 Uber doc-index anchor. In session `claude-web-2026-05-15-0310`, the boot card inlined `case:uber-driver-harassment-2026.summary_row` containing a scrubbed-claim phrase, and the entity slug encoded case theory. Both are spec-violating under C.3. A spec-compliant boot would have surfaced `case:uber-driver-harassment-2026` (structural slug only), zero inlined prose, and `critical_alerts.scrubbed_summaries_pending_regeneration: N`.

### C.10 Migration from non-compliant boot cards

Migration sequence: (1) Inventory phase — classify every `render_briefing_card()` field per §12.1; (2) Schema phase — replace orientation-grade fields with structural counterparts + retrieval hints; (3) Critical-alerts phase — implement all counters; (4) Skill-index phase — replace prose trigger lists with {skill_id, tags, priority, version_hash}; (5) Continuity-tail phase — surface only prior_session_id + continues_edge_id. Migration sequenced behind §3.5 + §14.1.

### C.11 Open questions

Should the boot card carry an explicit `boot_card_version` hash for §13.4 audit-backstop scope? Should `critical_alerts` carry per-counter `last_changed_at` timestamps? Should the section manifest carry per-section `freshness`? All deferred to v1.4 polish pass.


## Appendix D — Agent-time-of-use injection patterns

This appendix specifies concrete patterns for writing §12-compliant prompts. The four templates cover the main injection scenarios; all are reusable across agents and platforms. Each template names the grade it carries (§12.1), the evidence envelope it wraps (§12.2), and the citation anchor it must emit (§12.9).

### D.1 Lookup / retrieval (structural-grade)

**When to use:** retrieving a document path, URL, date, identifier, or any fact where the assertion is the only source.

**Template:**
```
[STRUCTURED_LOOKUP | source: assertion {assertion_id} | confidence: {confidence_score} | valid_from: {valid_from} | checked: {utc_now}]
Field: {field_name}
Value: {assertion.claim_value}
[/STRUCTURED_LOOKUP]
```

**Grade annotation:** `structural-grade` per §12.1. Carry-through obligation: any derived artifact that quotes this field inherits the structural grade and must cite assertion_id per §12.9.

### D.2 Context provision (structural-grade)

**When to use:** providing a batch of structural context — active assertions on an entity — before a reasoning task.

**Template:**
```
[CONTEXT_PROVISION
  | entity: {entity_id}
  | included_count: {n}
  | total_active_count: {m}
  | truncated: {true|false}
  | selection_strategy: {strategy}
  | selection_params: {params_json | none}
  | pulled_at: {utc_now}
  | cursor: {cursor_token | none}
  | content_hash: sha256:{hex}
]
{for each assertion in selected_assertions(entity_id, strategy, params):}
  assertion_id={id} predicate={predicate_form} claim={claim} confidence={confidence_score} valid_from={valid_from}
{end for}
[/CONTEXT_PROVISION]
```

**Grade annotation:** `structural-grade` per §12.1. Batch context never implies a synthesis; the agent must not generate orientation-grade prose from this block without an explicit synthesis step.

### D.3 Temporal qualification (structural-grade with freshness flag)

**When to use:** any fact with `valid_until` or `valid_from` that is time-sensitive for the task at hand.

**Template:**
```
[TEMPORAL_QUALIFIED | assertion_id: {id} | valid_from: {valid_from} | valid_until: {valid_until} | now: {utc_now} | freshness: {CURRENT|STALE|EXPIRED}]
Claim: {claim}
[/TEMPORAL_QUALIFIED]
```

**When freshness=STALE or EXPIRED:** the consuming agent MUST NOT quote the claim as current-state without a re-fetch or explicit temporal caveat.

### D.4 Uncertainty injection (belief-grade)

**When to use:** injecting an agent-believed claim (confidence < confirmed) or a hypothesis into a prompt chain.

**Template:**
```
[BELIEF_INJECTION | assertion_id: {id} | confidence: {confidence_score} | derivation: {derivation_type} | seeded_by: {seeded_by} | seeded_at: {created_at}]
Claim: {claim}
Reasoning: {reasoning_summary}
[/BELIEF_INJECTION]
```

**Grade annotation:** `belief-grade` per §12.1. Downstream agents receiving this block MUST treat its content as provisional; belief-grade content must not propagate into structural-grade derivations without a re-confirmation step.

### D.5 Injection-at-agent-time contract

All four templates share five invariants:

1. **Evidence envelope first.** The metadata block (`[...]`) MUST precede the claim content; the consumer processes the grade and freshness before reading the claim.
2. **Citation anchor mandatory.** Every injected assertion carries `assertion_id` so the consumer can emit a citation per §12.13 without a re-fetch.
3. **No prose laundering.** The template text is structural syntax; descriptive gloss that converts a structural field into orientation-grade prose violates §12.3's aggregation constraint.
4. **Admission-gated truncation.** Materializers MUST enforce both per-entity (D.2 `included_count`) and per-packet aggregate (sum across all D.* blocks in one dispatch) size limits. Overflow without an explicit `selection_strategy` raises rather than silently truncates. This is the §13 fail-closed posture applied to D.2. Truncated blocks MUST set `truncated: true` and provide a non-`none` `cursor`.
5. **Content-hash integrity.** D.2 blocks carry a `content_hash` over the canonicalized block body. Downstream consumers MAY verify the hash to detect mutation between materialization and consumption. This does not establish that a cited assertion backs a specific claim; it establishes only that the D.2 block has not been tampered with in transit.

### D.6 Anti-patterns

| Anti-pattern | Spec violation | Correct approach |
|---|---|---|
| `"The Uber case file is at /path"` (no assertion_id, no grade) | §12.2: missing evidence envelope; §12.9: missing citation anchor | Use D.1 template |
| `"Here is what we know about the Uber case: [orientation prose]"` from CONTEXT_PROVISION block | §12.1: grade laundering | Emit structural fields; separate synthesis step if needed |
| `"As of last session, ..."` without `valid_from` / `checked` | §12.7: temporal qualification omitted | Add temporal block; use D.3 template |
| Injecting a high-confidence structural assertion into a belief-grade context chain without flagging the downgrade | §12.1: implicit grade downgrade | Flag explicitly in the injection block |

### D.7 Relationship to skill-router scope

§8.2.12 adds derived-artifact-authoring to the skill-router trigger set. Templates in Appendix D are the canonical injection patterns for the skill-router's `source_injection` step in derived-artifact authoring. The skill-router MUST use D.1–D.4 templates for all substrate lookups during authoring.

## Resume / promote checklist

Track the spec's progression from draft to promoted public artifact. Each item is closed by a specific session and explicit evidence; items not closed remain open work.

- [x] **Draft body landed.** `cortex-provenance-substrate-v1.md` at `workspaces:universal-llm-gateway/docs/architecture/` (this session, `claude-web-2026-05-13-1806`). All twelve sections + two appendices.
- [ ] **SuperHeavy review dispatch.** Per `agent_skill:grok-web-dispatch`; substrate + load-bearing PDFs. Review questions covering principle generalization, prior-art completeness, failure-mode coverage, workflow-patterns scope, family/version granularity, supersession field-preservation contract. Output sidecar at `notes/system/threads/<thread>-superheavy-substrate-review.md`.
- [ ] **Independent reviewer dispatch (#2 + #3).** `openai/gpt-5.5` and `google/gemini-3-pro` or equivalent at family/version-independent classification. Three-reviewer pattern matches the brief-spec's review shape (Appendix B).
- [ ] **Apply review feedback.** Reconciliation edits; supersede assertions on the spec entity where reviewers contradicted or refined the draft.
- [ ] **Cross-reference into grant artifacts.** Add §X reference in `artifact:goose-grant-packet-v3` narrative and §Y reference in `artifact:epistemic-substrate-paper-draft` citing this spec as the architecture-layer formalization of the provenance pillar.
- [x] **Brief-spec § 9.2.5 thin pointer.** New § 9.2.5 inserted in `entity-backed-claim-provenance.md` between § 9.2 and § 9.3; three-sentence pointer routes seed-data verification at this phase to substrate § 5 + § 6 and `agent_skill:auditor-validatability-confidence` (landed v1.2). Closes `todo:provenance-spec-9-2-5-amendment` on the brief-implementation child.
- [ ] **Cortex entity update.** Mark `todo:cortex-provenance-substrate-spec` workflow_state from `in_progress` to `done` once all three master-close criteria are satisfied: (a) draft merged to workspaces canonical path, (b) brief-spec §9.2.5 demoted, (c) cross-references landed in grant artifacts.
- [ ] **`source_uri` and content_hash on the spec entity.** `entity_update(entity_id='document:cortex-provenance-substrate-v1', source_uri='workspaces:universal-llm-gateway/docs/architecture/cortex-provenance-substrate-v1.md')` to anchor the entity at the file and auto-recompute the content hash.
- [ ] **Promotion assertion on the spec entity.** Write a `confidence: confirmed` assertion on `document:cortex-provenance-substrate-v1` recording the promotion event (analogous to brief-spec assertion 9240).

Note: the brief-spec implementation arc (`todo:entity-backed-claim-provenance-implementation`) closes independently when the BOE-19-P §9.7 publish gate passes; it does not block this master.

---

*End of spec v1 draft.*
