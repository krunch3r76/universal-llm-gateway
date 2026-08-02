---
trigger_match_terms: ["auditor-validatable-confidence", "auditor_validatable_confidence", "assert", "entity", "confidence", "confirmed", "cortex-planning", "universal", "discipline", "calling", "cortex", "tool"]
description: "Universal discipline for any agent calling cortex(tool='assert', confidence='confirmed') — every confirmed-state attribute must be independently auditable from the entity card alone."
---

# Auditor-Validatable Confidence

**Version:** 1.0-compressed  
**Authority:** HIGH — universal for `assert(confidence='confirmed')`, `entity_create/update(status='confirmed')`, and confirmed `supersede` writes.

## Trigger

Read before:
- `cortex(assert, confidence='confirmed')`;
- `entity_create` / `entity_update` with `status='confirmed'`;
- `supersede` or `assert(supersedes_id=...)` where new confidence is `confirmed`;
- promoting `believed/suspected/hypothesized → confirmed`;
- writing typed attrs (`effective_date`, `decision_date`, `citation_canonical`, `holdings`, etc.) on a confirmed entity;
- seeding load-bearing bibliographic, exhibit, case-law, or reference data.

## Core rule

`confirmed(X) ⇒ independent_auditor_can_validate(X) using only entity_card(attributes + assertions + relationships) + cited sources`.

Single-source verbatim ingestion is not enough if the auditor must trust the originating agent. The auditor needs either direct verification path from URI, independent corroboration, or cryptographic/structural verifiability.

## Requirements for `confirmed`

For a confirmed assertion, and by extension confirmed parent entity, ALL hold:

1. **Every confirmed typed attribute has ≥1 backing confirmed assertion.** If one assertion supports only one attr, write separate assertions for other attrs. Do not let attrs ride on neighbor evidence.
2. **Claim embeds literal verbatim quote** from authoritative source when the claim depends on source text. Quote the actual text in quote marks — not “see URL,” not paraphrase, not sibling cross-reference.
3. **`evidence_uris` contains authoritative source URI.** Empty/null `evidence_uris` + confirmed is an audit failure.
4. **`derivation_type` matches evidence path.** Structured field must agree with evidence prose.
5. **Description factual claims are assertion-backed.** Description can orient, but load-bearing dates/names/provenance/citations require assertions satisfying this gate.
6. **Evidence path is independent.** Direct source verification, multi-source agreement, or cryptographic/structural verification. Single SuperHeavy/web-search/dispatch output ⇒ seed `believed` unless independently verified.

## Derivation mapping

| Evidence path | `derivation_type` |
|---|---|
| Agent directly fetched/read source | `direct_observation` |
| Agent inferred from sibling/format/analog | `inference` |
| Tool output observed by agent | `agent_observation` |
| User stated claim | `user_statement` |
| Verbatim quote of source text | `quotation` + `chunk_id` + `evidence_uris` |
| Compression of chunk into derived claim | `compression` + `chunk_id` + `evidence_uris` |
| Agent future commitment | `commitment` |

`derivation_type=direct_observation ∧ evidence says inferred_from_sibling ⇒ audit_fail`.

## Pre-write checklist

Before confirmed write:
1. Verbatim quote in claim, in quote marks, from authoritative source?
2. Source URI in `evidence_uris`?
3. `derivation_type` matches actual path?
4. Independent path exists: direct fetch OR independent source/model corroboration OR cryptographic/structural verification?
5. Parent confirmed entity: every typed attr has backing assertion?
6. Description: every load-bearing factual claim covered by assertion?

Optional: `assert(..., dry_run=true)` surfaces auditor `validation_warnings` **without** inserting — fix or `acknowledge_audit_gaps` / downgrade before the real write (`cortex-orientation` § Assert / entity write preflight).

If any fail: fix gap OR downgrade to `believed`/`suspected` with promotion gap in `reasoning_summary`. Downgrade is always available and safer than false confirmed.

## Examples

### Passes auditor

```python
cortex(tool='assert', arguments='{"entity_id":"legal_source:rtc-63.1","claim":"Current operative effective date of Cal. Rev. & Tax. Code § 63.1 is 2026-01-01 per amendment footer: \'(Amended by Stats. 2025, Ch. 539, Sec. 1. (SB 293) Effective January 1, 2026.)\'","confidence":"confirmed","evidence":"Direct fetch returned footer; quoted text exact-match.","evidence_uris":["https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=RTC&sectionNum=63.1"],"derivation_type":"direct_observation","valid_from":"2026-01-01"}')
```

Auditor can fetch URI, grep quote, and derive effective date.

### Fails auditor

Bad: claim says “SuperHeavy verbatim identical to § 7000” without embedding § 7001 quote; `derivation_type=direct_observation` while evidence is cross-reference/inference. Auditor must trust agent and traverse siblings. Fix by fetching actual source, embedding verbatim, and setting correct derivation.

### Correct downgrade

Single-source paste-in or blocked PDF extraction with no independent verification ⇒ `confidence='believed'`, evidence explains gap, `derivation_type='inference'` or accurate weaker path. Promote only after OCR/direct verification or corroboration.

## Anti-patterns

- “See URL” instead of embedded verbatim.
- “Identical to sibling entity X” shortcut.
- `direct_observation` when inferred.
- Promoting from one seat’s output alone.
- Confirmed entity attrs without backing assertions.
- Unsupported factual claims in descriptions.
- Supersede shortcut that drops `evidence_uris`, `derivation_type`, or `valid_from`.

## Related disciplines

- `entity-creation-discipline`: pre-create typed source-artifact verification.
- `no-silent-inference`: drafting-side equivalent of not laundering inference.
- `provenance-granularity`: model_id / independence gate contract.
- `named-entity-verification-gate`: external artifact ratification depends on auditor-validatable entities.
- `session-close-audit`: post-hoc gap detector; this is upstream write discipline.

## Origin / failure anchor

Session `web-2026-05-13-0239`: SuperHeavy confabulated a non-existent “C 6/19/2007” counsel-date marker for BOE Annotation 625.0036 by sibling pattern-completion; correct effective date was 1992-02-28. the operator's rule: “Whatever entity you designate confirmed, an independent auditor should be able to validate too based on the entity alone.” Keep this as the canonical prior-override anchor: source-looking output can still be pattern-completed.

## Minimal operating summary

`confirmed` means auditor-validatable, not “agent feels sure.” Embed quote, cite source URI, match derivation_type, ensure independent path, back every confirmed attr/description fact with assertions. Otherwise write `believed` and record promotion gap.
