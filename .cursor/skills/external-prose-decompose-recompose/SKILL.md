---
name: external-prose-decompose-recompose
description: >-
  On factual external prose rewrite — claim ledger, sense-or-exclude dispositions,
  earn-test for optional claims, recompose from outline not patch.
trigger_match_terms:
  - external-prose-decompose-recompose
  - claim ledger
  - decompose recompose
  - omit_with_reason
  - strategy_coverage
  - earn test
  - sense-or-exclude
  - retrieved writing samples
  - writing context
---

# External prose — decompose → recompose

`factual_external_prose ∧ (rewrite ∨ recompose ∨ provenance_audit) ⇒ Use this skill`.
Optional / description-triggered — ¬ always-on.

Composes: `prose-discipline` (craft) · `no-silent-inference` (presence grounding) ·
`writing-with-provenance` (absence / exclusion flags) ·
`consensus-steelman-posture` (material escalation) · `completion-provenance-discipline` (delivery).

Seed / charter: `cortex://notes/system/specs/external-prose-decompose-recompose-skill.md`
· `decision:external-prose-claim-ledger-gate`.

## Modes

| Mode | Shape |
|---|---|
| `light` | one model: decompose → audit → recompose → re-audit |
| `comparative` | ≥2 independent recompositions from frozen ledger; drop ineligible; blind genre judge |
| `material` | comparative + cross-family provenance verifier; consensus only on hard trigger |

`complexity` controls ceremony. Factual eligibility is non-waivable in every mode:
`unmatched ∨ over_strengthened ∨ stranded_material ⇒ ¬deliver`.

**Retained-stale caveat (a:25965):** the full-re-emission / stranded-material waiver
covers **DROPS** only. It does **not** cover retained-but-stale transition or
connective sentences after multi-turn patching. Any external letter built by
multi-turn patching gets ≥ **light** recompose (ledger + wire/claim re-audit of
retained transitions). Wire re-audit at minimum names: demonstratives, temporal
`since`/`after`, connective `that-is-why` / `which-is-why`.

## Standing rule — sense or exclude

**Disposition law SoT:** Use the `writing-with-provenance` skill
(`express|imply|omit_with_reason`, ¬silent_drop, proposition-scoped omit,
`letter-out ≠ fact-false`, DISPOSITIONS / gap grammar). ¬ re-encode that law here
(agent-bus:5202 / Fable challenge 1 — pointer cut 2026-07-16).

This section keeps only EPDR-local ceremony on top of that law:

**Earn test (binding for `express`):**
`express(C) ⇔ continuity(C, paragraph_topic) ∧ ¬invented_facts`.
Continuity means same barrier / same actor / same frame as the hosting beat.
`¬pass(earn_test) ⇒ ¬express` — choose `imply` or `omit_with_reason`.

**Forbidden:** force every token of C into one clause to satisfy inclusion
(number-stacks, “bridge the gap between A, rising to B, finally C”).

**Mutation seats** (smooth / FACT / breathe): when C is optional, emit
`DISPOSITIONS` outside the artifact per `writing-with-provenance` (fixed-field
omit reasons). Preserve omit reasons as negative-space provenance
(¬ resurrect without re-adjudication).

## Standing rule — retrieved writing samples

`∀ prompt that includes retrieved writing samples / writing context:`

Treat that block as **retrieved knowledge for craft and architecture** (why-grounding,
employer tie-back, human JD-parallel) — not optional flavor and not biography to copy.
Use steals unless they conflict with the fact ceiling or operator binds.
When adjudicating, note which samples steered you.

`¬` reason about retrieval pipelines, indexes, or tool names. If samples are missing and
the task needs exemplary priors, ask the orchestrating seat to attach them.

## Playbook

1. Freeze `fact_ceiling` separate from style-only writing context.
2. If retrieved writing samples are present, apply **Standing rule — retrieved writing samples**.
3. Build `strategy_coverage`; every material row gets a disposition (sense-or-exclude).
4. Decompose draft into atomic claims → claim ledger.
5. Repair before rewrite: stranded → fuse/move/cut; unsupported → ask/cut; uncovered strategy → address or `omit_with_reason`.
6. Recompose from audited strategy + claim outline — ¬ sentence-patch prior prose.
7. Treat fusion / rewrite / breathe as mutations; re-decompose and re-audit.
8. Judge only fact-eligible ∧ strategy-eligible candidates.
9. Record verifier falsifiers; ¬ cure missing provenance by vote count.

## Claim ledger (compact)

| Field | Role |
|---|---|
| id | stable claim id |
| proposition | atomic claim |
| wire | rhetorical job / beat |
| source | class + ref |
| strength | permitted |
| strategy | row coverage |
| status | ACCEPTED / REJECTED / optional-candidate |
| disposition | express / imply / omit_with_reason (+ reason if omit) |

Worst-plausible-reading: audit at the strongest reading a cold skeptic could take.

## Anti-patterns

| ✗ | ✓ |
|---|---|
| Must-keep optional claim → one squeezed clause | Earn test or `omit_with_reason` |
| Silent omit of strategy row | Disposition + provenance reason |
| REJECTED wire → wipe sibling ACCEPTED | Proposition-scoped omit only |
| Patch prior sentences across mutations | Recompose from ledger |
| Case facts as universal skill rules | Point to case sidecars only |
| Ignore attached writing samples | Use steals; note which steered |

Patch-across-mutations also fails the Modes **retained-stale caveat**: multi-turn
patched letters need ≥ light recompose + wire re-audit of retained transitions
(DROPS-only waiver does not cover stale connectives).

## Complexity notes

- `comparative`: exclude fact-/strategy-ineligible before blind judge.
- `material`: escalate per `consensus-steelman-posture`.
- Portable ledger may be Markdown (+ optional JSON); ephemeral entity cluster is a later seam — ¬ invent types here.
