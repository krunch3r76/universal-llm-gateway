---
name: cortex-provenance-discipline
description: "Before citing Cortex entities, assertions, relationships, or journals in derived artifacts — provenance discipline for substrate factual citations."
trigger_match_terms: ["cortex-provenance-discipline", "cortex_provenance_discipline", "cite", "quote", "cortex", "artifact", "cortex-planning", "reference", "entities", "assertions", "relationships", "edges"]
---

# Cortex Provenance Discipline

**Version:** 2.1-compressed  
**Authority:** HIGH — Cortex-substrate factual citations in derived artifacts.

## Trigger / scope

Read before citing, quoting, or referencing Cortex entities, assertions, relationships, session edges, or journals in a derived artifact (legal brief, demand letter, report, declaration, agent-to-agent dispatch).

This governs **Cortex-substrate factual citations**. Domain authorities (cases, statutes, regulations, treatises, financial mechanics, medical literature) use their own citation conventions — never encode them as `[assertion:NNNN]`.

## Core rule

```
∀ Cortex-substrate factual citation C in derived artifact:
  evidence_grade(C) ∧ [assertion:NNNN] grammar ∧ active(C) ∧ unconflicted(C) ∧ reader_defense_in_depth(C)
⇒ cite
```

Definitions:
- `active = superseded_by IS NULL ∧ (valid_until IS NULL ∨ valid_until > now)`.
- `unconflicted = no active contradicts edge from another assertion on same entity ∧ no newer active same-predicate assertion`.
- `same-predicate = predicate_form equality`.

## Substrate shape / evidence grade

| Substrate | Evidence status | Citation rule |
|---|---|---|
| Entity `id`, type, name, description, status, attrs | structural | To cite an attr/typed field, cite the backing assertion; structural fields aggregate evidence, not standalone proof. |
| Entity `summary_row` | orientation-grade | Never quote as fact; routing only. |
| Entity `terminal_facts` block (on `case:` / `account:` `entity_get`) | orientation-grade (machine-derived) | Leads, not citations: cite the backing assertion never the entry; check `epistemic_state`; `scope_truncated` ⇒ absence is not proof. |
| Assertion `id`, `entity_id`, `claim` | evidence-grade | Core factual payload. |
| Assertion `confidence`, `derivation_type`, `evidence_uris`, `chunk_id` | evidence-grade provenance | Inspect before citation. |
| Assertion `valid_*`, `superseded_by`, `predicate_form` | structural | Freshness/conflict checks. |
| Relationship | structural graph fact | Entity↔entity, type/role/strength; no session attribution. |
| Session/reasoning edge | cognitive/session-attributed | `extends`, `evidence_for`, `contradicts`, `corroborates`, etc.; do not treat as structural relationship. |
| Journal | episodic narrative | Never quote as fact without independent evidence-grade backing. |

## Confidence + derivation

| Confidence | Meaning |
|---|---|
| `confirmed` | Auditor-validatable + independent verification |
| `believed` | Source consulted, gate not fully satisfied |
| `suspected` | Plausible inference |
| `hypothesized` | Speculative |

Derivation types: `direct_observation`, `agent_observation`, `user_statement`, `quotation` (requires `chunk_id`), `compression`, `inference`, `other`.

Critical: `confirmed + user_statement` confirms the user stated X — not that X independently happened.

## Permitted-language mapping

| Evidence state | Permitted language |
|---|---|
| `confirmed` + non-inference derivation + active + unconflicted + `evidence_uris` | “X occurred” / “X is the case” |
| `confirmed` + `user_statement` | “User stated X” — NOT “X happened” |
| `confirmed` + `quotation` | “The source says X” / “The document records X” |
| `confirmed` + `inference` | “One inference is X” / “This is consistent with X” — flag as inference |
| `believed` | “Believed: X” / “It appears that X” |
| `suspected` | “Suspected: X” |
| `hypothesized` | “Hypothesis: X” |
| `compression` / orientation-grade summary | Insufficient for substantive claim; route to evidence only |
| `superseded` | Do not present as current; history only if explicit |
| `conflicted` | State conflict or withhold; never silently choose |

## Output citation grammar

Inline anchor adjacent to the claim:

```text
[assertion:NNNN]
```

Single source: `The notice was mailed 2025-08-27 [assertion:9847].`

Multiple sources: one bracket per supporting assertion, adjacent and unspaced; prefer atomic decomposition:

```text
The notice was timely mailed [assertion:9847][assertion:9851][assertion:9852].
```

Direct verbatim quotes: `[assertion:NNNN]` gives assertion provenance, but the quoted string itself MUST be verified against `chunk_id` / underlying source text. Do not transcribe the assertion `claim` as if it were the source.

`citation_count ≥ 8 ⇒ output_citation_high_cardinality`; decompose or use set-aggregating assertion.

Required for structural-grade Cortex-substrate claims in human-consumed output and load-bearing belief-grade claims. Not required for orientation prose or non-load-bearing conversation. Not applicable to domain-authority citations.

Blocking read-time findings:
- `output_citation_missing_assertion`: citation absent/unresolved/superseded on load-bearing ledger entry.
- `output_citation_semantic_mismatch`: cited assertion resolves but does not semantically support the claim. Cite the source, not pattern-completed plausible IDs.

## Claim ledger before drafting

For each material factual claim:
1. Identify source assertions by **reading the assertion `claim` field**, not ID pattern-match.
2. Inspect `confidence`, `derivation_type`, `evidence_uris`, `valid_from/until`, `superseded_by`, `predicate_form`.
3. Query same entity + predicate_form newest first; scan active `contradicts` edges.
4. Apply permitted-language mapping.
5. Record ledger internally or in comment.

## Reader defense at quote time

Before quoting/citing any assertion, verify:
- `superseded_by IS NULL`;
- `valid_until IS NULL ∨ valid_until > now`;
- no active `contradicts` edge from another assertion on same entity;
- no newer active same-`predicate_form` assertion;
- evidence-grade field only (not summary_row, journal, search snippet, description prose);
- exact `[assertion:NNNN]` grammar (no fuzzy resolution).

## Domain authority gate

For non-Cortex claims (legal doctrine/statutes/cases/regulations, financial mechanics, medical, etc.): declare source class — `substrate-cited`, `skill-cited`, or `unverified-priors`. Never launder priors as evidence-grade. Legal/regulatory citations follow domain conventions, not `[assertion:NNNN]`.

## Anti-patterns

- Quoting `summary_row` / orientation-grade fields as facts.
- Silent inference laundering (“the account is locked” vs “operator reports the account is locked”).
- Citing superseded assertions or active assertions without contradiction/newer-predicate scan.
- Transcribing assertion `claim` as verbatim quote without checking source.
- Omitting `[assertion:NNNN]` on load-bearing Cortex-substrate claims.
- Treating session edges as structural relationships, or structural relationships as session edges.
- Encoding domain authorities in `[assertion:NNNN]`.
- Pattern-completing plausible assertion IDs without verification.

## Failure anchors

> **§12.0 — 5 AM cascade** (spec-blessed). Session `claude-web-2026-05-15-0310`: scrubbed claim survived as `summary_row` and was quoted as fact; inference-derived assertion 9205 cited as reassurance with `derivation_type: inference` ignored; model priors substituted for substrate consultation. Spec-layer gaps, not model-capability gaps.

> **§13.1 — 2026-05-14 Uber doc-index supersedence** (spec-blessed). Session `claude-web-2026-05-14-1301`: doc-index quoted assertion 9020 three days after assertion 9023 established newer source as authoritative. Both had `superseded_by: NULL`; reader used filename heuristics instead of temporal authority/freshness discipline.

> **Anti-example regression — Phase B v1 draft** (2026-05-17, not spec-blessed; never reuse these IDs as support). SuperHeavy produced this citation grammar while violating it via pattern-completed plausible IDs — citing a password-change-email assertion for phishing-pretext claim. Lesson: read cited assertion text; do not pattern-match IDs.

## Cross-refs

`cortex-orientation` (write-side) · `auditor-validatable-confidence` (write-time gate) · `no-silent-inference` · `named-entity-verification-gate` · `case-evidence-retrieval` · `lawyer-stance`

## Minimal operating summary

Cortex-substrate claim → read assertion claim text → inspect confidence/derivation/freshness/conflict → apply permitted language → emit adjacent `[assertion:NNNN]` → run reader defense. Domain authorities use domain citations, not Cortex assertion IDs.
