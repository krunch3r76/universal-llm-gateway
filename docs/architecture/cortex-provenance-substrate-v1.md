# Cortex Provenance Substrate — Architecture Spec v1

**Version:** v1.2
**Audience:** spec readers without Cortex internals; Cortex agents (all seats, all platforms)
**Scope:** Universal write-time provenance discipline for the Cortex epistemic substrate
**Read model:** [`docs/cortex-spec.md`](../cortex-spec.md) — the entity/assertion/edge schema this spec layers discipline on top of
**Companion spec:** [`entity-backed-claim-provenance.md`](entity-backed-claim-provenance.md) — first domain instantiation (authored artifacts)

**Changelog:**
- v1.2 — Adds Appendix A subsection "Lineage and belief-revision lineages," clarifying Cortex's relationship to provenance semirings, Datalog / TMS lineage models, CRDT-based knowledge graphs, bitemporal property graphs, and AGM / Hansson belief-revision substrates. Descriptive related work; no normative protocol changes.
- v1.1 — Post-initial-draft refinements ahead of external substrate review.
- v1.0 — Initial draft.

---

> **Note on citations.** This spec is self-contained: every architectural claim
> stands on its own argument. Inline tokens such as `service:cortex assertion N`,
> `transcript:<session>`, `agent-bus:<thread>`, `todo:<slug>`, and `cortex://...`
> resource IDs are durable references into the project's private Cortex knowledge
> graph — they form a provenance trail for the maintainers, not load-bearing
> reading for an outside audience, and can be skipped. (Worked `cortex://` URIs
> shown as *examples of the URI grammar* are illustrative and self-explanatory.)
> The empirical figure cited throughout — long-horizon recall accuracy rising
> from 61.6% (similarity-only) to 93.3% (prospective-indexed) — is from the
> Kumiho LoCoMo-Plus benchmark.

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
| `quotation` | Verbatim quote of source text at chunk granularity. | `chunk_id` required (RAG-deterministic ID `{content_hash_prefix}-{i}`, resolved via `libs/cortex_store/rag_resolver.py`); `evidence_uris` MUST contain the URI of the chunk's parent source. |
| `compression` | Compression of a chunk into a derived claim that summarizes or paraphrases. | `chunk_id` required; `evidence_uris` MUST contain the parent source URI. |
| `commitment` | Agent commitment to do something in the future — the claim is performative, not descriptive. | `evidence` string identifies the commitment context. |
| `stated` | Generic stated claim with no narrower derivation_type fit; rare. | `evidence` string supplied. |
| `other` | Reserved escape hatch. | `evidence` string MUST justify why none of the above fit. |

The taxonomy is derived from TROVE (quotation / compression / inference / other) — already Cortex's foundation per `service:cortex` assertion 101 — extended with the observation types (`direct_observation`, `agent_observation`, `user_statement`) required to handle agent-tool-mediated evidence and direct user input, plus `commitment` for performative claims and `stated`/`other` as escape hatches.

### 3.2 Chunk binding for `quotation` and `compression`

A `chunk_id` resolves to a contiguous span of a previously-ingested document. After Phase E (master @ 25a2260a), RAG is the authoritative chunk store; cortex assertions reference chunks by RAG-deterministic ID. The ingestion path is:

1. RAG ingest writes the source and returns chunk IDs of the form `{content_hash_prefix}-{i}`.
2. Write an assertion with `chunk_id` (the RAG-deterministic ID) + `evidence_uris[0]` (the source URI RAG indexed). The cortex resolver — `libs/cortex_store/rag_resolver.py::resolve_assertion_chunk` — fetches the exact span by calling `POST /api/v1/rag/chunks_by_index`.

For `derivation_type: quotation`, the claim text contains the literal verbatim from the chunk, in quote marks, and the chunk-id binding gives an auditor a deterministic way to fetch the exact passage the claim is quoting. For `derivation_type: compression`, the claim text summarizes/paraphrases the chunk and the chunk-id binding gives the auditor the source span the compression must be faithful to.

A `quotation` assertion whose claim text does not actually contain the quoted span is a structural-field-vs-claim-text mismatch and is the §5.2 audit failure mode rendered at the structured-field level. A `compression` assertion whose claim asserts facts beyond what the chunk supports is the §5.5 failure mode rendered at the structured-field level.

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

### 8.2 The eleven finding kinds

The detector classifies gaps along three axes: **claim-layer** (artifact-to-graph), **seed-data-layer** (entity-to-backward-evidence), and **forward-provenance-layer** (assertion-to-forward-projection). The eleven kinds:

**Claim-layer (artifact references the graph):**

1. `missing_backing_entity` — claim token (citation, named entity, exhibit ref) does not resolve to any entity in the graph. **Severity: high.**
2. `unverified_entity` — token resolves to an entity, but the entity has zero assertions. The shell exists; the substance does not. **Severity: medium.**
3. `unverified_claim` — token resolves to an entity with assertions, but no `corroborates` reasoning edge from an independent verifier exists for the specific claim being made. **Severity: low.**
4. `contradicted_claim` — token resolves and assertions exist, but a `contradicts` reasoning edge from an independent verifier flags the specific claim. **Severity: critical.**
5. `verbatim_check_failed` — token's quotation in the artifact does not match the chunk_id-bound assertion's claim text (after §4.3 normalization). **Severity: high.**

**Seed-data-layer (entity references backward evidence):**

6. `missing_attribute_backing` — entity at `status: confirmed` has a typed attribute with no backing assertion (§5.1 R1). **Severity: high.**
7. `missing_evidence_uri` — assertion at `confidence: confirmed` has `evidence_uris: null` or empty (§5.1 R3). **Severity: high.**
8. `derivation_type_mismatch` — assertion's `derivation_type` does not match the prose in `evidence` (§5.1 R4). **Severity: medium.**
9. `description_unbacked_claim` — entity's description makes a factual claim with no backing assertion (§5.1 R5). **Severity: medium.**

**Forward-provenance-layer (assertion-to-forward-projection):**

10. `missing_prospective_summary` — assertion at `confidence: confirmed` has `prospective_summary: null` (§4.7.1). **Severity: low** — the claim is verifiable backward but unindexed for future cue-trigger retrieval; not blocking but degrades future-recall quality. The cortex write surface auto-generates `prospective_summary` by default, so this finding flags either (a) pre-v3 assertions not backfilled, or (b) write-time auto-generation failures.
11. `events_json_invalid` — assertion's `events_json` is either (a) `null` when the claim text describes a temporally-located event, or (b) populated but with a triple inconsistent with the claim text (event doesn't match; consequence contradicts the claim; temporal off by more than the assertion's `valid_from`/`valid_until` window). **Severity: medium** — invalid event structure compromises downstream causal-reasoning over the supersede chain.

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
