# Entity-Backed Claim Provenance — Architecture Spec v1

**Status:** v1
**Authors:** Opus 4.7 (drafter), gpt-5.5 / gemini-2.5-pro / Opus 4.7-on-cursor-seat (reviewers — see Appendix B for independence disclosure)
**Origin session:** web-2026-05-12-2121
**Strategic frame:** cortex assertion 9149 on `project:universal-llm-gateway`
**Source materials:**
- `notes/legal/property-tax/dispatch/entity-provenance-schema-substrate.md` — review substrate
- `notes/legal/property-tax/dispatch/entity-provenance-schema-responses.md` — Q-by-Q reviewer comparison
- `agent-bus:968` — review thread, five turns

---

## Abstract

This spec defines a knowledge-graph architecture in which every load-bearing claim of an authored artifact (legal brief, scientific paper, regulatory filing, engineering specification, medical chart, financial analysis) must trace to a typed entity carrying a URI to the primary source the claim derives from. Claims that fail to resolve to a backing entity are surfaced automatically as structural gaps. Where the claim asserts a verbatim quotation, an independent model reads the cited source and emits a `corroborates` or `contradicts` reasoning edge against the claim — closing the loop on cross-model verification at write time, not as a post-hoc audit.

The architecture is a direct generalization of the existing consensus-pipeline invariant in `universal-llm-gateway/libs/provenance`: where the pipeline today gates inter-model verification on `originator_model_id != evaluator_model_id`, this spec generalizes the originator slot from "another model" to "a primary-source authority entity," so the same `is_independent` check, the same `validate_provenance_present` gap detector, and the same lineage tracking carry over unchanged. Only the type of the originator changes.

The load-bearing public claim: **structural impossibility of un-backed claims by construction.** The *Mata v. Avianca* and *Park v. Kim* hallucinated-citation failure modes become not "rarer with better RAG" but architecturally inaccessible — the graph refuses to consider a claim resolved if its citation does not point to a typed authority entity, and refuses to mark a quotation `confirmed` if an independent model has not read the source and corroborated.

---

## § 0. Strategic frame

The architectural thesis (assertion 9149) is that the consensus pipeline's core invariant — **independence + provenance + automated verification gates produce higher-quality outputs** — generalizes from "originator = another model" to "originator = a primary-source authority." The pipeline architecture, the `is_independent` check, and the `validate_provenance_present` gap detector all carry over unchanged.

The domain-portable consequence: legal briefs, scientific papers, regulatory filings, engineering specs, medical diagnoses, financial analyses — anywhere claims chain back to verifiable authorities, the same architecture applies. This spec instantiates the architecture for legal documents (the BOE-19-P appeal brief is the first concrete substrate), but the entity types, URI scheme, and verification flow are domain-portable.

The spec is written for a public-artifact target: entity-id grammar, naming choices, and document structure assume a reader who has never seen Cortex internals, so the spec can be lifted into a paper without retrofitting.

---

## § 1. Entity types

Three entity types make up the core schema. One is new (`legal_source:`), one already exists in the graph and is formalized here (`case-law:`), one is new and scoped (`exhibit:`). A fourth pre-existing type (`case:`) is referenced but not modified — it remains the active-matter container.

### 1.1 `legal_source:` — abstract primary authority (NEW)

For statutes, regulations, agency letters, official publications, annotations, treatises, model rules. Stable canonical citation; reusable across matters.

**ID grammar:** `legal_source:<jurisdiction>-<corpus>-<id>[-<version>]`

Examples:
```
legal_source:rtc-63.2                       # Cal. R&T Code § 63.2
legal_source:ccr-18-462.520                 # 18 CCR § 462.520
legal_source:boe-lta-2022-012               # BOE LTA 2022/012
legal_source:boe-pub-800-1-2025-rev-may     # BOE Pub. 800-1 (Rev. May 2025)
legal_source:boe-ah-401                     # BOE Assessors' Handbook § 401
legal_source:probate-code-7000              # Cal. Prob. Code § 7000
legal_source:aba-mr-1-1                     # ABA Model Rule 1.1
```

**Required fields:**
- `type = "legal_source"`
- `name` — full Bluebook-form citation
- `source_uri` — authoritative URL (leginfo, BOE, official publisher) OR workspace corpus path

**Required attributes:**

| Field | Type | Notes |
|---|---|---|
| `citation_canonical` | string | "Cal. Rev. & Tax. Code § 63.2" |
| `citation_short` | string | "§ 63.2" — for in-text reuse |
| `authority_class` | enum | `statute` \| `regulation` \| `agency_letter` \| `publication` \| `annotation` \| `treatise` \| `model_rule` \| `probate_code` |
| `jurisdiction` | string | ISO-style (`CA`, `US`, `EU`) |
| `effective_date` | ISO date \| null | when the authority took effect; null for undated |
| `superseded_by` | `legal_source:*` \| null | for amended/repealed authorities |
| `aliases` | list\[string\] | citation forms used in briefs ("§ 63.2", "R&T § 63.2", "Section 63.2") |

**Rationale:** `legal_source:` is the load-bearing new type. It collapses what existing legal-AI systems treat as flat citation strings into typed graph entities, enabling structural lookup, alias resolution, supersession tracking, and verbatim-chunk addressability (§ 3).

### 1.2 `case-law:` — cited precedent (EXISTING — formalize attribute set)

Already in use (e.g. `case-law:larson-v-duca-1989`). This spec does not rename; it formalizes the required attribute set so the type can carry the same verification machinery as `legal_source:`.

**ID grammar:** `case-law:<short-name>-<year>`

Examples:
```
case-law:larson-v-duca-1989                 # Larson v. Duca, 213 Cal.App.3d 324
case-law:mcdonald-v-antelope-valley-2008    # McDonald v. Antelope Valley CCD, 45 Cal.4th 88
case-law:ard-v-contra-costa-2001            # Ard v. County of Contra Costa, 93 Cal.App.4th 339
case-law:mata-v-avianca-2023                # Mata v. Avianca, Inc. (S.D.N.Y. 2023)
```

**Required fields:**
- `type = "case-law"`
- `name` — full citation string
- `source_uri` — CourtListener / Justia URL OR workspace opinion path

**Required attributes:**

| Field | Type | Notes |
|---|---|---|
| `citation_canonical` | string | "Larson v. Duca, 213 Cal.App.3d 324 (1989)" |
| `pinpoint_default` | string | default pin Cortex tracks for this entity's primary proposition |
| `court` | string | "Cal.App. 1st Dist." |
| `decision_date` | ISO date | |
| `procedural_posture` | string \| null | "appeal_from_summary_judgment" — optional |
| `holdings` | list\[string\] | each item a propositional summary of one holding |
| `treatment` | string | `good_law` \| `limited_by:<case-law:>` \| `overruled_by:<case-law:>` |
| `aliases` | list\[string\] | short-form citations used in briefs |

### 1.3 `exhibit:` — case-specific factual document (NEW)

For documents whose evidentiary value is bound to one matter (notices, decrees, signed forms, photographic evidence). Scoped under the parent case.

**ID grammar:** `exhibit:<case-slug>/<exhibit-slug>`

The `/` separator after the type prefix is **confirmed accepted** by Cortex's ID column. Probe: `test:_slash_probe_2026_05_12/sub-part-a` was created, retrieved via `entity_get`, and deprecated cleanly during session `claude-web-2026-05-12-2204`. No fallback to `-` separator is needed.

Examples:
```
exhibit:boe19p-flintridge-appeal-2026/supplemental-notice-2026-01-16
exhibit:boe19p-flintridge-appeal-2026/decree-of-distribution-2025-05-02
```

**Required fields:**
- `type = "exhibit"`
- `name` — e.g. "Exhibit 2 — Supplemental Assessment Notice (Jan 16, 2026)"
- `source_uri` — workspace path to scanned or native artifact

**Required attributes:**

| Field | Type | Notes |
|---|---|---|
| `exhibit_number` | string | the brief's "Exhibit N" label |
| `document_kind` | enum | `notice` \| `decree` \| `letter` \| `form` \| `statement` \| `photo` \| `recording` |
| `issuer` | string or entity-ref | "Santa Clara County Assessor" or `person:<slug>` |
| `document_date` | ISO date | |
| `authentication_basis` | enum | `mailed_original` \| `official_record` \| `screenshot` \| `photocopy` \| `affidavit_attested` |

**Required relationship:** `(this exhibit) belongs_to (case:<case-slug>)` — created at write time, not optional.

**Rationale (Q2 unanimous reviewer decision):** Scoping by case in the ID itself prevents cross-case ID collisions by construction. The `belongs_to` relationship is preserved for graph traversal regardless of URI lexer behavior. Slash-in-id is a tooling test, not an ontology objection.

### 1.4 Reserved: `case:` (NOT MODIFIED)

`case:` remains the active-matter container. Cited precedent uses `case-law:`. This disambiguation was confirmed unanimously by all three reviewers (Q4) and is binding: assertion 9147 (which loosely referred to precedent as "`case:`") is superseded by assertion 9149 and this spec.

---

## § 2. URI scheme & pinpoint fragments

Citations between assertions and source entities flow through `cortex://` URIs in the `evidence_uris` field of an assertion. The URI scheme is extended in v1 to carry a **pinpoint fragment**.

### 2.1 URI grammar

```
cortex://<entity-id>[#<pinpoint>]
```

Examples:
```
cortex://legal_source:rtc-63.2#f-1-B           # subdivision (f)(1)(B)
cortex://legal_source:rtc-63.2#e-9             # subdivision (e)(9)
cortex://case-law:larson-v-duca-1989#327       # page 327
cortex://case-law:mcdonald-v-antelope-valley-2008#para-12   # paragraph 12
cortex://exhibit:boe19p-flintridge-appeal-2026/supplemental-notice-2026-01-16  # no pinpoint
```

### 2.2 Resolver extension (Q1 unanimous reviewer decision)

The existing `resolve(uri, tag?)` operation is extended to honor `#fragment` and return a tuple:

```python
resolve("cortex://legal_source:rtc-63.2#f-1-B")
  → { entity: <legal_source:rtc-63.2 entity card>,
      pinpoint: "f-1-B",
      chunk: <chunk in source's chunk manifest matching f-1-B>,
      verbatim: "Notwithstanding subparagraph (A), a claim shall be deemed..." }
```

If no fragment is supplied, `resolve` returns the entity card without a chunk. If a fragment is supplied but does not match any chunk in the source's manifest, the call returns `pinpoint_unresolved` with the entity card — diagnostic for misformed citations.

Rationale for choosing extend-resolver over the alternatives:

- **Schema-change alternative** (turning `evidence_uris` into `list[dict]` with `{uri, pinpoint}` tuples): forces an assertion-table migration affecting every existing assertion that carries evidence URIs, with no offsetting capability gain.
- **Entity-explosion alternative** (one entity per subdivision): inflates entity count by ~50–500× per authority, makes supersession tracking brittle, pollutes search/activation surfaces.
- **Chosen path** localizes complexity to one resolver, preserves `list[str]` shape of `evidence_uris`, aligns with web-standard URI fragment semantics.

### 2.3 Pinpoint format per source type

The pinpoint string is opaque to the resolver — it's a key into the source's chunk manifest. Conventional formats per `authority_class`:

| Authority class | Pinpoint format | Example |
|---|---|---|
| `statute`, `regulation`, `probate_code` | subdivision-tree dotted path | `f-1-B` = (f)(1)(B); `e-9` = (e)(9) |
| `agency_letter` | section number or Q-N | `q-41`; `section-iii` |
| `publication` | page or section number | `p-12`; `section-3` |
| `annotation` | annotation number | `625-0036` |
| `treatise`, `model_rule` | section + comment | `mr-1-1-cmt-8` |
| `case-law` | star page or paragraph | `327`; `para-12`; `star-329` |

Pinpoints are stable, not version-specific. Cross-version stability (statute amendments) is handled by the `superseded_by` chain on the entity, not by the pinpoint format.

---

## § 3. Source ingestion & chunking

This is the spec's resolution of Q3 — the one substantive divergence in the reviewer pass. Two reviewers (gpt-5.5 and the cursor-seat Opus 4.7) explicitly endorsed the hybrid framing the spec adopts. Gemini twice endorsed pre-splitting (which the unified rule absorbs as the statute/reg special case). The unified rule:

### 3.1 One canonical entity per authority

Each authority is **one** `legal_source:` or `case-law:` entity. Subdivisions, pages, paragraphs do not become entities — they become chunks under the authority's chunk manifest. This avoids entity-count inflation and keeps supersession tracking at the authority level.

### 3.2 Structure-aware chunking at ingest

When `ingest_document(source_uri, content, authority_class=...)` runs, a **chunker is selected by authority class:**

| Authority class | Chunker | Native unit |
|---|---|---|
| `statute`, `regulation`, `probate_code` | subdivision-tree chunker | one chunk per leaf subdivision (e.g. (f)(1)(B)) |
| `agency_letter` | section-aware chunker | one chunk per numbered section or Q&A |
| `publication` | section/page chunker | one chunk per section heading; fallback to page boundary |
| `annotation` | single-chunk | the annotation is the chunk |
| `treatise`, `model_rule` | section+comment chunker | one chunk per rule + per official comment |
| `case-law` | page+paragraph chunker | one chunk per page, with paragraph anchors as secondary index |

Each chunk's `chunk_id` carries the pinpoint label that becomes the URI fragment. Chunks are addressable by `(authority_entity_id, pinpoint)` tuple in the source's manifest.

### 3.3 Rationale

Statutes and opinions require different chunkers because their structural primitives differ. Treating them uniformly is the false choice the binary forces. The unified rule absorbs all three reviewer positions:

- Pre-split (gemini): yes — every authority is chunked at ingest, not searched at verification time.
- Structure-aware (gpt-5.5, cursor): yes — the chunker's structural unit matches the authority class's native granularity.
- One canonical entity (all three): yes — no source becomes a child entity. The graph stays flat at the authority level; depth lives in chunks.

### 3.4 Verbatim quotations resolve to chunks

When an assertion has `derivation_type = "quotation"` and `evidence_uris = ["cortex://<authority>#<pinpoint>"]`, the verbatim-enforcement check (§ 6) resolves the fragment to the chunk and normalized-text-matches the claim against the chunk's content.

---

## § 4. Claim-assertion grammar

A load-bearing sentence in an authored artifact becomes an assertion on a **synthesis entity** — either the whole artifact or a section of it. The synthesis entity is the anchor for all claim-assertions in that section, so the structural-gap detector (§ 7) has a clean traversal entry point.

### 4.1 Synthesis-entity naming

```
brief:<case-slug>-v<version>                    # whole-brief anchor
brief:<case-slug>-v<version>/<section-slug>     # per-section anchor
```

Example: `brief:boe19p-flintridge-appeal-2026-v6/argument-i-a` is the anchor for assertions deriving from the brief's Argument § I.A.

### 4.2 Two principal derivation types

**Verbatim quotation:**

```python
assert(
  entity_id        = "brief:boe19p-flintridge-appeal-2026-v6/argument-i-a",
  claim            = "Notwithstanding subparagraph (A), a claim shall be deemed "
                     "to be timely filed if it is filed within six months after "
                     "the date of mailing of a notice of supplemental or escape "
                     "assessment, issued as a result of the purchase or transfer "
                     "of real property for which the claim is filed.",
  derivation_type  = "quotation",
  chunk_id         = "<chunk in legal_source:rtc-63.2 with pinpoint 'f-1-B'>",
  evidence_uris    = ["cortex://legal_source:rtc-63.2#f-1-B"],
  confidence       = "confirmed",
)
```

**Multi-source synthesis:**

```python
assert(
  entity_id        = "brief:boe19p-flintridge-appeal-2026-v6/argument-i-b",
  claim            = "Under § 63.2(e)(9) read against Probate Code §§ 7000-7001, "
                     "beneficial ownership for change-in-ownership vests at the "
                     "decree of distribution, not at death.",
  derivation_type  = "inference",
  evidence_uris    = ["cortex://legal_source:rtc-63.2#e-9",
                      "cortex://legal_source:probate-code-7000",
                      "cortex://legal_source:probate-code-7001",
                      "cortex://case-law:larson-v-duca-1989#327"],
  reasoning_summary = "Larson distribution-date holding survives in § 63.2 "
                      "because § 63.1(c)(1) override was not re-enacted.",
  confidence       = "believed",
)
```

### 4.3 Required-attribute summary for claim assertions

Every claim-assertion must carry:

| Field | Required when | Notes |
|---|---|---|
| `entity_id` | always | synthesis entity at the section level |
| `claim` | always | the proposition in prose |
| `derivation_type` | always | `quotation` \| `inference` \| `direct_observation` \| `agent_observation` |
| `evidence_uris` | always | ≥1 `cortex://` URI. Zero URIs is a structural gap. |
| `confidence` | always | `confirmed` \| `believed` \| `suspected` \| `hypothesized` |
| `chunk_id` | when `derivation_type ∈ {quotation, compression}` | binds the assertion to the specific chunk it derives from |
| `reasoning_summary` | recommended for `inference` | explains how the cited sources combine to support the claim |
| `valid_from` | required for date-bearing claims | per existing Cortex assertion contract |

### 4.4 Structural gap is defined by absence

An assertion with empty `evidence_uris` is, by this spec's definition, a structural gap. The verbatim text of the brief may contain a citation, but if it does not encode that citation as a `cortex://` URI resolving to an entity, the claim is unbacked. The detector (§ 7) surfaces these mechanically; no human review is needed to detect the gap, only to remediate it.

---

## § 5. Cross-model verification flow

The legal-doc analog of the consensus pipeline's cross-model verification. Once a claim-assertion is written, an independent model can read its cited source(s) via `resolve(uri, tag?)` and emit a verification.

### 5.1 Verifier protocol

1. **Verifier model picks up a synthesis-entity assertion tree.** Dispatch via `team_dispatch(role=reviewer, model=<verifier_model_id>)` or `frontier_dispatch(model=<verifier_model_id>)` with the synthesis-entity id and the assertion ids as context.

2. **For each claim assertion in the tree, the verifier:**
   - Reads each `evidence_uri` via `resolve(uri, tag?)`, pulling entity + chunk + verbatim text.
   - For `derivation_type="quotation"`: confirms the claim text appears (normalized) in the resolved chunk.
   - For `derivation_type="inference"`: assesses whether the cited authorities, read together, support the claim.

3. **The verifier writes a verification assertion on the same synthesis entity:**
   ```python
   assert(
     entity_id        = "brief:boe19p-flintridge-appeal-2026-v6/argument-i-a",
     claim            = "Verbatim quotation in claim X confirmed against "
                        "legal_source:rtc-63.2#f-1-B; normalized text match.",
     derivation_type  = "agent_observation",
     evidence_uris    = ["cortex://legal_source:rtc-63.2#f-1-B"],
     confidence       = "confirmed",
     reasoning_summary = "Verifier read the resolved chunk and matched normalized text.",
   )
   ```
   The verification assertion records the verifier's `model_id` in its originator slot. The Cortex storage detail — which field, attribute, or relationship encodes the originator `model_id` — is a Cortex schema concern resolved separately from this spec.

4. **The verifier `edge_create`s from the verification assertion to the target claim assertion:**
   ```python
   edge_create(
     from_node   = "<verification_assertion_id>",
     to_node     = "<target_claim_assertion_id>",
     edge_type   = "corroborates",   # or "contradicts"
     strength    = 1.0,
     context     = "Verifier read legal_source:rtc-63.2#f-1-B and confirmed verbatim match.",
   )
   ```

### 5.2 Independence gate

Before the verifier writes, `is_independent(target_provenance, verifier_model_id)` must return True. The originator slot for a claim assertion is captured in `seeded_by` (model identity); the verifier's `model_id` is the evaluator.

**Granularity:** the check compares model identity at the family/version level (e.g. `openai/gpt-5.5`, `google/gemini-2.5-pro`, `anthropic/claude-opus-4-7`), not at the seat or platform level. Same model on different seats does not satisfy independence. (See § 10.5 for the empirical illustration from this spec's own consult.) The existing `libs/provenance::is_independent` should be audited during § 9.1 to confirm it compares at family/version granularity rather than session/seat granularity.

### 5.3 Edge-endpoint implementation caveat


Cortex's `edges` table accepts assertion-to-assertion endpoints. The verification edge anchors directly on the target claim assertion with `from_node` and `to_node` both pointing at assertion IDs.

Verified via `todo:cortex-edge-endpoint-namespaced-id-validation` (closed 2026-05-04; assertion 8290 records the implementation, edge IDs 5763 and 5764 are the working examples). The cursor turn-5 caveat about entity-only endpoints was answering a deployment uncertainty that has since been resolved; the entity-anchored fallback pattern is no longer required.

### 5.4 Verification yields aggregate confidence

A synthesis-entity assertion tree can be queried for its verification coverage:

```python
def verification_coverage(synthesis_entity_id):
    claims = list_claim_assertions(synthesis_entity_id)
    verified = [c for c in claims if has_corroborates_edge(c)]
    contradicted = [c for c in claims if has_contradicts_edge(c)]
    unverified = [c for c in claims if not (verified or contradicted)]
    return {
        "verified": len(verified),
        "contradicted": len(contradicted),
        "unverified": len(unverified),
        "verifier_models": distinct_verifier_models(verified + contradicted),
    }
```

Aggregate coverage is what the publish gate (§ 6.2) consults.

---

## § 6. Verbatim enforcement

Two-tier discipline per Q6 unanimous (b) soft-flag + gpt-5.5/cursor nuance:

### 6.1 Tier 1 — Write-time soft flag

At assertion-write, when `derivation_type="quotation"`:

1. Resolve the `evidence_uri` to a chunk.
2. Normalize both the claim text and the chunk's content per § 6.3.
3. If normalized claim appears as a substring of normalized chunk: write with `confidence="confirmed"`.
4. Else: write with `review_status="flagged"` and a `quotation_check_result` attribute containing:
   - `status: "mismatch"`
   - `chunk_id: <resolved chunk>`
   - `claim_normalized: <text>`
   - `chunk_normalized: <text>`
   - `closest_match: <best longest-common-substring or null>`

The assertion lands either way. The flag is the gate.

### 6.2 Tier 2 — Publish-gate hard fail

At document render time (DOCX generation for filing, PDF generation for review, etc.):

1. Walk the render tree's assertion graph.
2. If any quotation assertion in the tree has `review_status="flagged"`: refuse to render.
3. Surface the flagged assertions with their `quotation_check_result` details.
4. Reviewer (human or independent verifier model) must clear the flag — either by correcting the claim text to match the chunk, by correcting the cited chunk to match the brief, or by explicit override (`review_notes` documenting the deliberate paraphrase).

### 6.3 Normalization spec

The normalization rules below are the v1 minimum — implementation-defined, intentionally permissive at Tier 1 so iterative drafting isn't rejected. Tighter algorithm specification (Unicode normalization choice, outer quote-mark stripping, citation-insertion patterns to elide) is deferred to § 10.6.

Normalization applied before substring matching:

- Lowercase.
- Collapse runs of whitespace (including newlines) to single space.
- Strip leading/trailing whitespace.
- Treat `...` (ellipsis) as a wildcard that matches any non-empty span in the chunk.
- Treat `[word]` (square-bracketed substitution) as a wildcard matching either `word` or the chunk's original word at that position.
- Treat smart quotes (`"` `'` `'` `'`) and straight quotes (`"` `'`) as equivalent.
- Treat hyphens and en-dashes / em-dashes as equivalent.
- Reject if normalized claim contains content not in normalized chunk (after ellipsis/bracket wildcard substitution).

The normalization spec is intentionally permissive: legal quotations routinely involve ellipses, brackets, and excerpting, and a strict matcher rejects correct quotations during iterative drafting (the Tier-1 brittleness gpt-5.5 flagged). Strictness lives at Tier 2's publish gate.

### 6.4 Anti-patterns

- **Pure asynchronous verification** defaults `review_status` to clean and creates a window where unverified quotations may be consumed downstream — wrong failure direction for citation-heavy artifacts.
- **Hard-fail at write** blocks legitimate iterative drafting; legal practitioners cycle through dozens of quotation edits per section.
- **Skipping Tier 2** lets flagged quotations survive into filed documents; this is the failure mode that produced *Mata v. Avianca*.

The two-tier discipline matches the existing `evidence-review-discipline` and `pre-assert-skeptic-pass` skills: soft at first contact, hard at consequential publish.

---

## § 7. Structural-gap detector

Direct port of `libs/provenance/cross_model.py::validate_provenance_present`, applied to authored artifacts.

### 7.1 Detector pseudocode

**Citation-token extraction surface.** v1 follows the TROVE bracketed-sentence-ID convention (Document 1 `【N】`-style markers, with classification into quotation/compression/inference/other — the same TROVE taxonomy already adopted as Cortex's `derivation_type` per assertion 101) and PaperTrail's atomic/faithful/decontextualized claim decomposition (PaperTrail § 3.1, span annotation via NLTK punkt tokenizer + programmatic matching). The authored artifact is assumed to be structured markdown with inline citation markers — Bibliographic-Index references like `[ref:N]`, italicized case names (`*Larson v. Duca*`), and `Exhibit N` labels. The BOE-19-P brief v6-corrected is the v1 conformance corpus.

`parent_case` is **exhibit-scope-only** — it resolves `Exhibit N` tokens to `exhibit:<case-slug>/<exhibit-slug>`. It is unused for `legal_source:` and `case-law:` token resolution.

```python
def detect_structural_gaps(artifact_path, parent_case=None):
    """Walk the artifact, find citation tokens, check each resolves to an entity."""
    sections = parse_sections(artifact_path)
    findings = []
    for section in sections:
        citation_tokens = extract_citation_tokens(section)
        # bracketed [ref:N] refs (Bibliographic Index)
        # italicized case names ("*V v. V*")
        # "Exhibit N" labels (resolved against parent_case)
        # bare statute citations (§ X.Y(Z), R&T § X, Prob. Code § X)
        for token in citation_tokens:
            entity = resolve_citation_to_entity(token, parent_case)
            if entity is None:
                findings.append(GapFinding(
                    section=section.id,
                    token=token,
                    kind="missing_backing_entity",
                    severity="high",
                ))
                continue
            if entity.assertion_count == 0:
                findings.append(GapFinding(
                    section=section.id,
                    token=token,
                    kind="unverified_entity",
                    severity="medium",
                ))
                continue
            if has_contradicts_edge_from_independent_verifier(
                    section, token, entity):
                findings.append(GapFinding(
                    section=section.id,
                    token=token,
                    kind="contradicted_claim",
                    severity="critical",
                ))
                continue
            if section_quotation_assertion_flagged(section, token, entity):
                findings.append(GapFinding(
                    section=section.id,
                    token=token,
                    kind="verbatim_check_failed",
                    severity="high",
                ))
                continue
            if not has_corroborates_edge_from_independent_verifier(
                    section, token, entity):
                findings.append(GapFinding(
                    section=section.id,
                    token=token,
                    kind="unverified_claim",
                    severity="low",
                ))
    return findings
```

**Finding-kind severity ordering:** `critical` (contradicted_claim) > `high` (missing_backing_entity, verbatim_check_failed) > `medium` (unverified_entity) > `low` (unverified_claim). The publish gate (§ 6.2) refuses to render on any `critical` or `high` finding.

### 7.2 Resolution: alias lookup

`resolve_citation_to_entity` runs the citation token through:

1. Exact `citation_canonical` match.
2. Exact `citation_short` match.
3. `aliases` list match per `legal_source:` / `case-law:` entity.
4. Normalized substring match (last resort, surfaces near-misses for reviewer attention).

A citation token like "§ 63.2" resolves to `legal_source:rtc-63.2` via citation_short or aliases; "Cal. Rev. & Tax. Code § 63.2" via citation_canonical; "*Larson*" italicized via case-law aliases.

### 7.3 Concrete example — BOE-19-P brief v6-corrected

Brief § I.B references "BOE Annotations 220.0263 and 625.0090" inline. Bibliographic Index contains entry [11] for annotation 625.0036 but no entries for 220.0263 or 625.0090. Detector findings on this brief:

```
GapFinding(
  section="argument-i-b",
  token="BOE Annotation 220.0263",
  kind="missing_backing_entity",
  severity="high",
)
GapFinding(
  section="argument-i-b",
  token="BOE Annotation 625.0090",
  kind="missing_backing_entity",
  severity="high",
)
```

These are real structural gaps in the live brief that this architecture surfaces mechanically without requiring a human re-read.

### 7.4 Findings surface as graph artifacts

Detector findings are themselves graph entities, surfaced as either:

- **Flagged assertions** on `parent_case` with `predicate_form="missing_backing(<citation>)"` (or `contradicted_claim`, `verbatim_check_failed`, etc.) and `review_status="flagged"`.
- **`gap:` entities** with `type="gap"` linked to `parent_case` via a `surfaces` relationship.

The choice depends on the deployment's preference for review-queue surfacing vs entity-graph traversal; both are valid. The default is flagged assertions because the review queue already handles `flagged` as a workflow state.

All five finding kinds (`contradicted_claim`, `verbatim_check_failed`, `missing_backing_entity`, `unverified_entity`, `unverified_claim`) surface through the same channel; severity determines render-gate enforcement (§ 6.2) and review-queue prioritization.

### 7.5 The structural-impossibility guarantee

Once `detect_structural_gaps(artifact, case)` returns zero `critical` and zero `high` findings AND `verification_coverage(synthesis_entity)` shows zero unverified or contradicted claims, the artifact cannot contain hallucinated citations by construction. Every citation token resolves to an entity; every entity has a `source_uri` to a primary source; every quotation has been verbatim-checked or flagged; every claim has been corroborated by an independent verifier or contradicted (and contradictions surface as critical findings before render).

This is the load-bearing market story per assertion 9149. The *Mata v. Avianca* failure mode is the citation token that resolves to nothing in the graph (`missing_backing_entity`). The *Park v. Kim* failure mode is the citation that resolves to an entity that was never fact-checked against its `source_uri` (`unverified_entity` or `unverified_claim`). Both are detected mechanically, not by reviewer attention. A third failure mode — the claim that an independent verifier read and *rejected* — becomes a `contradicted_claim` finding at `critical` severity, gating render until resolved.

---

## § 8. Naming and namespace summary

### 8.1 Type-prefix conventions

| Type prefix | Status | Purpose |
|---|---|---|
| `legal_source:` | NEW | abstract primary authority (statute, regulation, agency letter, publication, annotation, treatise, model rule) |
| `case-law:` | EXISTING, formalized | cited precedent (judicial opinion) |
| `exhibit:` | NEW, case-scoped | case-specific factual document |
| `case:` | UNMODIFIED | active matter container |
| `brief:` | NEW | synthesis entity for authored artifact; versioned; per-section sub-anchors |
| `gap:` | OPTIONAL | structural-gap detector finding (alternative: flagged assertions) |

### 8.2 Reserved namespaces this spec does not touch

`doc:`, `decision:`, `todo:`, `person:`, `transcript:`, `family:`, `role:`, `agent_skill:`, `plan_phase:`, `legal_doctrine:`, `project:`, `model_family:`.

### 8.3 Authority class enum

`statute` | `regulation` | `agency_letter` | `publication` | `annotation` | `treatise` | `model_rule` | `probate_code`

### 8.4 Document kind enum (exhibit)

`notice` | `decree` | `letter` | `form` | `statement` | `photo` | `recording`

### 8.5 Authentication basis enum (exhibit)

`mailed_original` | `official_record` | `screenshot` | `photocopy` | `affidavit_attested`

### 8.6 Derivation types (extended from existing Cortex set)

| Type | Requires | Use |
|---|---|---|
| `quotation` | `chunk_id` + `evidence_uris` | verbatim text from a source |
| `inference` | `evidence_uris` (≥1), recommended `reasoning_summary` | synthesis combining multiple sources |
| `direct_observation` | — | deterministic read (e.g. exhibit's `document_date`) |
| `agent_observation` | `evidence_uris` (the sources read) | verifier output (§ 5) |

---

## § 9. Migration & rollout

### 9.1 Phase 1 — Schema registration (one session, no entity writes yet)

1. Register `legal_source:` type in Cortex's type registry with the attribute schema from § 1.1.
2. **[RESOLVED PRE-SPEC]** Cortex's ID column accepts `/` after the type prefix. Verified via probe `test:_slash_probe_2026_05_12/sub-part-a` during session `claude-web-2026-05-12-2204`. Register `exhibit:` with `/`-scoped grammar.
3. Register `exhibit:` type with the attribute schema from § 1.3.
4. Verify `case-law:` type's attribute schema matches § 1.2; backfill required attributes on existing `case-law:` entities that lack them.
5. **[RESOLVED PRE-SPEC]** Cortex's `edges` table accepts assertion-to-assertion endpoints. Verified during `todo:cortex-edge-endpoint-namespaced-id-validation` (closed 2026-05-04; assertion 8290). § 5.3 uses the direct-assertion-anchor pattern.
6. Extend `resolve(uri, tag?)` to honor `#fragment` per § 2.2.
7. Extend `ingest_document` to accept `authority_class` and dispatch to the correct chunker per § 3.2. **Chunker rollout sequencing:** Phase 1 ships dispatch + the subdivision-tree chunker (highest precision need; covers statutes, regulations, probate_code, the largest fraction of Phase 2 authorities). Other class chunkers ship per-class as Phase 2 entities are seeded; each authority class's first ingestion validates its chunker against the live source.
8. Register the `brief:` synthesis-entity type.
9. Audit `libs/provenance::is_independent` to confirm it compares model identity at family/version granularity (per § 5.2). If it currently compares at session/seat granularity, the spec's independence gate is unenforced — flag for fix before Phase 5 verification.

### 9.2 Phase 2 — Bibliographic Index seeding (one session)

Seed the 14 authorities in `case:boe19p-flintridge-appeal-2026`'s Bibliographic Index as `legal_source:` and `case-law:` entities. Each gets a `source_uri` pointing to either an external authoritative URL or the existing workspace corpus at `universal-llm-gateway/docs/research/legal-reasoning/ca-property-tax/`. See Appendix A for the full mapping.

**AGM expansion framing.** Phase 2 deliberately seeds entities *without* claim-assertions. This is an AGM expansion operation — adding new beliefs (entities + their structural metadata) consistent with the existing graph, not revising existing beliefs. Per `service:cortex` assertion 1854 (Kaywan directive 2026-04-08), AGM compliance is foundational Cortex architecture; this pattern conforms.

**Closure-pass enrichment.** The session-close enrichment pass (per `agent-skills/enrichment-quality-discipline.md` Step 3c, pending the `audit_dispositions` extension per `todo:cortex-api-session-close-audit-dispositions`) fills each seeded entity with **structural assertions** by reading its `source_uri`:

- `effective_date`, `jurisdiction`, `citation_canonical`, `citation_short`, `aliases` (per § 1.1) for `legal_source:`
- `court`, `decision_date`, `holdings`, `treatment`, `pinpoint_default`, `aliases` (per § 1.2) for `case-law:`

These assertions are `derivation_type=direct_observation` (deterministic reads from the source) and carry the `source_uri` as their evidence URI. They are not claim-about-the-source assertions; those land in Phase 4 on synthesis entities.

After the close-pass enrichment, the detector's `unverified_entity` finding clears for each enriched entity. `unverified_claim` findings persist until Phase 4 (citation-bearing claim assertions on synthesis entities) and Phase 5 (verifier corroborates edges).

The pattern across phases: **expansion at Phase 2 (entity creation), enrichment at close (structural assertions), citation work at Phase 4 (claim assertions on synthesis entities), verification at Phase 5 (corroborates edges).**

### 9.2.5 Auditor-validatability discipline applies at this phase

Seed-data verification during § 9.2 (Bibliographic Index seeding) and § 9.3 (Exhibit seeding) is governed by the universal auditor-validatability discipline specified in `cortex-provenance-substrate-v1.md` § 5 (confirmed-confidence operational requirements) and § 6 (cross-model independence gate). Every `legal_source:`, `case-law:`, or `exhibit:` entity promoted to `status='confirmed'` MUST be validatable by an independent LLM auditor from the entity card alone — attributes + assertions + relationships — without access to the originating session's context; operational details (verbatim-quotation requirement, source URI grounding, derivation-type co-requirements, family/version independence granularity) live in the universal spec. See also `agent_skill:auditor-validatable-confidence` for the per-write checklist.

### 9.3 Phase 3 — Exhibit seeding (one session)

Seed the brief's referenced exhibits (Exhibits 2, 3, 5, 7, etc.) as `exhibit:` entities with `belongs_to` relationships to `case:boe19p-flintridge-appeal-2026`. Existing scanned artifacts already exist at `notes/legal/property-tax/dispatch/archived/`.

### 9.4 Phase 4 — Single-section backfill (one session)

Use § I.A of the brief as the test bed:

- Create `brief:boe19p-flintridge-appeal-2026-v6` (whole) and `brief:boe19p-flintridge-appeal-2026-v6/argument-i-a` (section).
- For each load-bearing sentence in § I.A, write a claim-assertion per § 4.
- Run `detect_structural_gaps`. Iterate until zero high-severity findings.

### 9.5 Phase 5 — Verification pass (one session)

Dispatch an independent verifier model (one whose `model_id` differs from the originator of the § I.A claim assertions) to verify each claim. Verifier follows § 5 protocol.

### 9.6 Phase 6 — Full brief backfill (multi-session)

Apply Phases 4–5 to remaining sections (§ 0, Statement of Facts, § I.B, § II, § III, § IV, Relief).

### 9.7 Phase 7 — Gap-detector publish gate (deployment)

Wire `detect_structural_gaps` and the verbatim-enforcement publish gate (§ 6.2) into the DOCX render pipeline. Refuse to render if any high-severity gap finding or flagged quotation is present in the render tree.

---

## § 10. Open issues for v2

### 10.1 Multi-citation pinpoint ranges

A single claim may quote a span of subdivisions or pages. The current spec assumes one pinpoint per `evidence_uri`. Extending to ranges (e.g. `cortex://legal_source:rtc-63.2#f-1-A..f-1-C` or `cortex://case-law:larson-v-duca-1989#326-328`) is deferred.

### 10.2 Archival and supersession of authorities

When a statute is amended, the original `legal_source:` entity should be preserved with a `superseded_by` pointer to the new version. Pinpoints stay stable per § 2.3, but assertions citing the old version may need re-anchoring to the new version's pinpoints. Migration semantics deferred.

### 10.3 Agent identity binding in verification lineage

The verifier's model identity is recorded in `seeded_by`, but a single verification run may chain multiple models (e.g. gemini retrieves the chunk via a tool call, gpt-5.5 evaluates the match). The lineage tracking in `libs/provenance::Provenance` handles this; the verification assertion should serialize the full lineage in a `lineage` attribute. v2 specifies the serialization format.

### 10.4 Verifier dispatch-shape priming sensitivity

Session web-2026-05-12-2121 observed `google/gemini-2.5-pro` returning Q6=(b) soft-flag via MCP-tool-loop dispatch (turns 3 and 4 on bus thread 968) but Q6=(a) hard-fail via `frontier_dispatch` with inline substrate. Same model, same substrate content, different answers. Operational implication: verifier dispatch shape may bias quotation-enforcement strictness. v2 should specify a canonical dispatch shape for verifier panels.

### 10.5 Same-model panel input non-independence

Cursor's review (turn 5 on bus thread 968) was authored by Claude Opus 4.7 on the cursor seat — the same model class as the drafter (Opus 4.7 on web). `is_independent(target_provenance, verifier_model_id)` returns False for this pairing. Cursor honestly disclosed the non-blind read at the top of its reply. Operational implication: panel-review independence must be enforced at the `model_id` level, not the seat level. v2 should encode an `is_panel_independent` check that diffs on model class.

This is a useful empirical illustration of why the consensus pipeline's independence gate compares originator IDs at the model granularity — even when a "different seat" suggests independence, the underlying model identity controls the epistemic relationship.

### 10.6 Normalization spec for quotation matching

§ 6.3 defines a permissive v1 minimum and explicitly defers the full algorithm. v2 specifies the normalization grammar formally so quotation matches are deterministic and reproducible across implementations. Specific sub-items:

- **Unicode normalization choice.** NFKD vs NFC vs no normalization. PDF/OCR-ingested chunks may carry different Unicode normalization than the brief's markdown source; the matcher needs a chosen canonical form. NFKD is the candidate (folds compatibility forms that real PDF extraction produces).
- **Outer quote-mark stripping rules.** Claim text in a brief is typically wrapped in `"..."` while the chunk's source text isn't. v2 specifies whether outer quotes are stripped before normalization or included in the matched string.
- **Editor-annotation patterns to elide.** Legal quotations routinely include `[citation omitted]`, `(emphasis added)`, `(footnotes omitted)`, `[internal quotation marks omitted]`. § 6.3's `[word]` bracketed-substitution rule covers `[Petitioner]`-style word replacements but not these phrasal annotations. v2 specifies the elision pattern list.
- **Wildcard matching algorithm.** Multiple `...` ellipses in one quote require a regex/NFA matcher, not simple substring scanning. v2 specifies the reference implementation (likely regex compilation of normalized claim against normalized chunk).
- **Published test corpus.** Real-world legal quotations from the BOE-19-P brief v6-corrected, with expected match/flag outcomes per quotation, serving as the conformance suite.

Suggested v2 dispatch: a technical-reasoning model (grok-4.20-high-reasoning, gpt-5.5, or gemini are all candidates — not Opus-class, to preserve independence from the drafter).

### 10.8 Cross-jurisdictional handling

The spec assumes single-jurisdiction citation grammar (CA in the BOE-19-P case). Federal authorities (USC, CFR) and other states' statutes need extension to the `legal_source:` id grammar. v2 specifies the grammar:
- `legal_source:us-usc-<title>-<section>`
- `legal_source:us-cfr-<title>-<part>-<section>`
- `legal_source:ny-cpl-<section>` (for state codes by ISO state)

### 10.9 Authority alias resolution

Citations in briefs use multiple forms ("Cal. Rev. & Tax. Code § 63.2" / "§ 63.2" / "R&T Code § 63.2" / "Section 63.2"). The `aliases` field (§ 1.1) carries the alias list, but v2 should specify the canonical alias-generation rules per `authority_class` so the structural-gap detector's resolution is reproducible.

### 10.10 Treatment chain enforcement

`legal_source.superseded_by` and `case-law.treatment` enable supersession tracking, but assertions citing an overruled `case-law:` should be flagged automatically. v2 encodes the propagation rule: when treatment transitions to `overruled_by:*`, all claim-assertions with `evidence_uris` referencing the entity get `review_status="flagged"` with an attached `treatment_change` attribute.

---

## Appendix A — Bibliographic Index mapping for `case:boe19p-flintridge-appeal-2026`

| Ref | Authority (brief Bibliographic Index) | Entity ID |
|-----|---------------------------------------|-----------|
| [1] | Cal. Rev. & Tax. Code § 63.2 | `legal_source:rtc-63.2` |
| [2] | BOE LTA 2022/012 | `legal_source:boe-lta-2022-012` |
| [3] | BOE LTA 2021/008 | `legal_source:boe-lta-2021-008` |
| [4] | 18 CCR § 462.520 | `legal_source:ccr-18-462.520` |
| [7] | BOE LTA 2009/004 | `legal_source:boe-lta-2009-004` |
| [8] | Cal. Rev. & Tax. Code § 1605 | `legal_source:rtc-1605` |
| [10] | Cal. Rev. & Tax. Code § 5151 | `legal_source:rtc-5151` |
| [11] | BOE Annotation 625.0036 | `legal_source:boe-annotation-625-0036` |
| [13] | BOE Pub. 800-1 (Rev. May 2025) | `legal_source:boe-pub-800-1-2025-rev-may` |
| [15] | Ard v. Contra Costa (2001) | `case-law:ard-v-contra-costa-2001` |
| [16] | McKnight Ranch v. FTB (2003) | `case-law:mcknight-ranch-v-ftb-2003` |
| [17] | Christopher P. v. Mojave USD (1993) | `case-law:christopher-p-v-mojave-usd-1993` |
| [18] | McDonald v. Antelope Valley CCD (2008) | `case-law:mcdonald-v-antelope-valley-ccd-2008` |
| [22] | BOE Assessors' Handbook § 401 | `legal_source:boe-ah-401` |

**Cited but absent from Bibliographic Index — structural gaps the detector should surface:**

| Brief citation | Status |
|---|---|
| *Larson v. Duca* (1989) — § I.B | Entity exists (`case-law:larson-v-duca-1989`), but absent from Bibliographic Index — fixable by either (a) adding to Index, or (b) accepting in-text citation as sufficient for case-law |
| BOE Annotation 220.0263 — § I.B | No entity yet → gap finding (`missing_backing_entity`) |
| BOE Annotation 625.0090 — § I.B | No entity yet → gap finding |
| Cal. Probate Code §§ 7000, 7001, 11640 — § I.B | No entities yet → gap findings |
| Cal. Rev. & Tax. Code § 63.1 — § I.B discussion | No entity yet → gap finding |

**Brief-referenced exhibits → `exhibit:` entities to seed:**

| Brief label | Document | Entity ID |
|---|---|---|
| Exhibit 2 | Supplemental Assessment Notice (Jan 16, 2026) | `exhibit:boe19p-flintridge-appeal-2026/supplemental-notice-2026-01-16` |
| Exhibit 3 | BYV Notification (Dec 16, 2025) | `exhibit:boe19p-flintridge-appeal-2026/byv-notification-2025-12-16` |
| Exhibit 5 | Determination & Corrected BOE-19-P (Sept 12, 2025) | `exhibit:boe19p-flintridge-appeal-2026/corrected-boe19p-2025-09-12` |
| Exhibit 7 | Decree of Distribution (May 2, 2025) | `exhibit:boe19p-flintridge-appeal-2026/decree-of-distribution-2025-05-02` |

(Remaining exhibits enumerated during Phase 3 seeding.)

---

## Appendix B — Reviewer attribution and independence disclosure

The schema underwent a three-reviewer consult on `agent-bus:968` during session `web-2026-05-12-2121`. Reviewer details:

| Reviewer | Dispatch | Independence | Confidence profile |
|---|---|---|---|
| `openai/gpt-5.5` | `team_dispatch(role=reviewer)` + MCP; execution `eadfa49c`; 88s | **Independent** — different model class from drafter (`anthropic/claude-opus-4-7`) | 5 high / 1 medium (Q5) |
| `google/gemini-2.5-pro` | `team_dispatch(role=reviewer)` + MCP; replied via `agent_bus.reply` tool call during MCP tool loop (dispatcher marked the dispatch failed at the final turn but the reply landed on the thread). Two such posts: turns 3 and 4 on bus thread 968. | **Independent** — different model class from drafter | 6 high (bus-posted answers). `frontier_dispatch` with `mcp=False` and inline substrate yielded a different Q6 answer — see § 10.4. |
| Claude Opus 4.7 (cursor seat) | Manual trigger by Kaywan; turn 5 on bus thread 968, posted 22:02:58 (14 min after session_close) | **NOT INDEPENDENT** — same model class as drafter (Opus 4.7 on web). Cursor disclosed at top of reply that it read turns 2–4 before answering. | 5 high / 1 medium (Q5). Substantively converged with gpt-5.5 on Q3 (hybrid framing) and on the Q5 edge-endpoint caveat. |

**Implications of cursor's non-independence:**

- Cursor's review is third-reviewer panel input with the caveat that strict independence is broken.
- The two strictly independent reviewers (gpt-5.5, gemini) cover all six questions; cursor's convergence with gpt-5.5 on Q3 strengthens that view but does not constitute a third independent vote.
- The same-model panel non-independence is itself usefully illustrative — see § 10.5.

**Empirical findings beyond Q1–Q6:**

- **Gemini priming sensitivity (§ 10.4):** Q6 answer differed across dispatch shapes on the same substrate. Bus-posted MCP-loop replies (both) returned (b) soft-flag; `frontier_dispatch` inline-substrate returned (a) hard-fail.
- **Gemini empty-completion behavior:** Two `team_dispatch` calls were marked failed at `turns_used=3, finish_reason=STOP` because the dispatcher saw empty top-level content. In both cases the model had already posted its review via `agent_bus.reply` during turns 1–2 of the MCP loop. The "failure" classification was incorrect for these dispatches.
- **Cursor turn-5 disclosure:** Cursor explicitly disclosed non-blind read and signed its reply with model identity. The disclosure is the right behavior; the architectural fix is the `is_panel_independent` check (§ 10.5).

---

## Related work

Already-curated prior art in `universal-llm-gateway/docs/research/`:

**Prior-art standards and architectures:**

- `temporal-provenance/w3c-prov-{dm,o,n,constraints}.html` — W3C PROV is the prior-art standard this spec extends. PROV provides Entity / Activity / Agent primitives; this spec adapts to a knowledge-graph-native setting with `derivation_type` and `is_independent`.
- `temporal-provenance/papertrail-claim-evidence-provenance.pdf` — closest sibling architecture. Encodes claim-evidence relationships but does not include cross-model verification gates.
- `temporal-provenance/trove-fine-grained-text-provenance.pdf` — text-provenance precursor at sub-document granularity, comparable to this spec's chunk-level pinpoints.
- `temporal-provenance/openlineage-object-model.html` — lineage-tracking model for data pipelines; this spec's `lineage` attribute serializes the same shape.
- `belief-consistency/graphcheck-kg-powered-fact-checking.pdf` — KG-based fact-checking baseline. This spec extends with cross-model verification gates and verbatim quotation enforcement.

The § 7.1 inline citation marker convention (TROVE-style `[ref:N]` and italicized case names within structured markdown) and § 7.1's `extract_citation_tokens` surface follow TROVE's bracketed-sentence-ID convention (TROVE Figure 1, `【N】` markers) and PaperTrail's atomic/faithful/decontextualized claim decomposition (PaperTrail § 3.1). The TROVE derivation taxonomy (`quotation` / `compression` / `inference` / `other`) is also the prior art for Cortex's `derivation_type` field (per assertion 101).

**Legal-AI failure modes this spec renders structurally inaccessible:**

- `legal-reasoning/mata-v-avianca-findlaw-full-text.html` — the original ChatGPT-hallucinated-citations sanction (S.D.N.Y. 2023).
- `legal-reasoning/park-v-kim-second-circuit-ai-hallucination-sanction.pdf` — Second Circuit sanction extending Mata to appellate practice.
- `legal-reasoning/large-legal-fictions-legal-hallucinations.pdf` — Stanford empirical study on legal hallucination prevalence across LLMs.
- `legal-reasoning/hallucination-free-rag-legal-tools-assessment.pdf` — Stanford assessment of commercial RAG-based legal tools.
- `legal-reasoning/aba-formal-opinion-512-generative-ai.pdf` — ABA Formal Opinion 512 on generative AI and Model Rule 1.1 (competence). This spec is a Rule 1.1-enabling architecture.

**Capability baselines:**

- `legal-reasoning/gpt-4-passes-the-bar-exam.pdf` — establishes the LLM legal-reasoning capability baseline against which this architecture's verification gates apply.
- `legal-reasoning/saullm-{7b,54b-141b}.pdf` — domain-adapted legal LLMs; complementary (model-side) vs this spec (architecture-side).

---

## Resume / promote checklist

Before promoting this spec from `cortex:notes/architecture/` to `workspaces:universal-llm-gateway/docs/architecture/`:

1. ✅ Reconcile against additional context Kaywan introduces in the fresh session — confirmed `None` (external context) and `Nothing that I am aware of` (unshared cursor content) in session `claude-web-2026-05-12-2204`. The § 1–§ 3 design and assertion 9149 strategic frame are uncontested.
2. ✅ Re-read this draft end-to-end after additional context has been integrated — done in session `claude-web-2026-05-12-2204`. Revisions captured: § 5.3 retired (Q5 edge-endpoint caveat resolved pre-spec via assertion 8290); § 5.1 step 3 dropped `seeded_by` field-name speculation per Kaywan's framing that originator-field is a Cortex schema concern; § 6.3 marked as v1 minimum with sub-items moved to § 10.6; § 7.1 added TROVE/PaperTrail precedent and two finding kinds (`contradicted_claim` critical, `verbatim_check_failed` high); § 7.4/§ 7.5 updated to reference five-kind taxonomy; § 9.1 narrowed prerequisites from two to one (slash-in-id remains, edge-endpoint resolved) and added `is_independent` granularity audit; § 9.2 reframed around AGM expansion + closure-pass enrichment per `agent-skills/enrichment-quality-discipline.md` and `todo:cortex-api-session-close-audit-dispositions`.
3. ✅ Both § 9.1 schema-registration prerequisite questions are now resolved pre-spec: slash-in-id (verified by probe `test:_slash_probe_2026_05_12/sub-part-a` during session `claude-web-2026-05-12-2204`); edge-endpoint granularity (verified by assertion 8290 during session `web-2026-05-04-0049`). No entity-creation gating remains before Phase 1.
4. Sequence Phase 2 seeding only after § 9.1 is settled.
5. ✅ Promotion landed 2026-05-12 (session `claude-web-2026-05-12-2328`):
   - ✅ Assertion 9149 promoted `staged` → `committed` on `project:universal-llm-gateway`.
   - ✅ Assertion 9147 superseded by 9229 on `case:boe19p-flintridge-appeal-2026` with corrected `case-law:` terminology per §1.4.
   - ✅ §10.4 verifier dispatch-shape priming-sensitivity finding seeded as assertion 9227 on `model:gemini-2.5-pro` (new entity, `child_of` `family:gemini`).
   - ✅ §10.5 same-model-panel input non-independence finding seeded as assertion 9228 on `model:claude-opus-4-7` (new entity, `child_of` `family:claude`). Seeded at model-version granularity rather than family-level because the finding's whole point is that `is_independent` must compare at the `model_id` level.

---

*End of v1 draft.*
