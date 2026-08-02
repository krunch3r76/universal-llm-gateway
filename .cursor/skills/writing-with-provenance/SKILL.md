---
name: writing-with-provenance
description: "Factual drafting/revision — disposition each material claim (express|imply|omit_with_reason); gaps visible; primary-search gate on new cites; proposition-scoped repair; ship-gate unresolved gaps."
trigger_match_terms: ["writing-with-provenance", "provenance writing", "disposition", "omit_with_reason", "exclusion", "negative-space", "fact ceiling", "REJECTED", "ACCEPTED", "unverified", "citation gate", "gap", "evidence class", "ship gate", "claim record", "blast radius"]
related_skills: ["no-silent-inference", "external-prose-decompose-recompose", "cortex-provenance-discipline", "completion-provenance-discipline", "evidence-review-discipline", "named-entity-verification-gate"]
---

# Writing with provenance

Presence twin: `no-silent-inference` (verify-or-mark before stating a detail). This skill governs the whole claim set of a factual artifact — what enters, what stays out, and how both remain auditable. Neither craft preference nor verification failure may silently reshape the fact record.

You are rendering a **claim record**: each material claim has an epistemic state and a rendering disposition; keep the record true and the rendering no stronger than the record supports. Skill (compose-time), DISPOSITIONS grammar (artifact-adjacent), and pipeline lint (durable) enforce **one discipline at successive durability tiers** — not parallel tracks.

**Applied from:** Fable 5 mechanism deliverable `…/fable-deliverable.md` (sha `f975bbd0…`, agent-bus:5202) + vision dialectic `…/fable-vision-dialectic.md` (sha `57de000f…`, agent-bus:5204). Operator ratified 2026-07-16: materiality floor + entity-ref strength; operator bound 2026-07-16: **r2** (top-tier total decomposition), **r3** (URI-fallback = substrate debt; pipeline detector), short blast-radius ladder in this skill.

## Trigger

`(draft ∨ revise ∨ adjudicate) factual artifact with material claims ⇒ use this skill`

**Blast-radius ladder** (graded obligations; wider external consequence ⇒ lower floor, more durable record):

| Tier | Scope | Decomposition | Floor |
|---|---|---|---|
| counterparty / filed / published | external consequence | **total** — every claim enters the ledger | gates **verification depth** only, not ledger membership (r2) |
| substrate-derived / durable-internal | Cortex-backed or durable internal | material claims per floor below | ratified materiality floor; considered-and-floored claims get a one-line note |
| conversational | casual chat | exempt unless invoked or facts consequential | unchanged |

`material(C) ⇔ load_bearing(C, artifact_purpose) ∨ externally_consequential(C)` — counterparty-bound, filed/published, money, dates, identities, obligations, technical invariants. At mid-tier this floor still selects which claims enter the ledger; at counterparty/filed tier membership is total and the floor only scopes how deep verification goes.

Decide the allowed fact set **before** expanding rhetoric or polish.

## Core law

A factual artifact is a **rendering of a claim record**. Every material claim carries two independent coordinates — **epistemic state** (`backed | unverified | gap`) and **rendering state** (`express | imply | omit_with_reason`) — bound by a **compatibility law**: no artifact may render a claim stronger than its epistemic state supports. The ship gate is the whole-record compatibility check; the evidence-class table below is that compatibility relation written out.

∀ material candidate claim C — candidates come from the request, the prior draft, Cortex/RAG/background retrieval, or the model's own knowledge:

| Obligation | Predicate |
|---|---|
| Disposition | `disposition(C) ∈ {express, imply, omit_with_reason}`; `missing ∉ dispositions` |
| No silent mutation | `mutation removes ∨ weakens C ⇒ DISPOSITIONS line outside the artifact` |
| Provenance state | `state(C) ∈ {backed, unverified_marked, gap_flagged}` — there is no fourth, silent state |
| Scope | `REJECTED(C) ∨ omit(C) ⇏ redisposition(siblings(C))`; `letter-out ≠ fact-false` |

`background_present(C) ⇏ artifact_in(C)` — a Cortex hit, RAG chunk, or prior-draft sentence is a candidate until dispositioned.

## Gap grammar (greppable)

| State | Form |
|---|---|
| backed | claim + source ref per artifact convention: entity/assertion id, URI, file §, quote locator |
| unverified | inline `[unverified: <what is unconfirmed>]` |
| omitted | `DISPOSITIONS` block outside the artifact — `C<n>: omit_with_reason — <reason class>: <cite>` |

One line per claim, fixed fields: the block exists for diffing and lint, not commentary. Markers are **debt, not decoration** — the destination is a resolvable source ref (a typed entity with a URI where substrate exists); the marker is the interim state and never the ship state.

**Entity-ref strength (operator-ratified):** when Cortex substrate exists, `substrate_derived ∨ counterparty_bound` artifacts ⇒ **require** entity-backed refs for load-bearing claims; elsewhere **prefer** entity refs with fallback to URI / file locator. Casual drafts without a live graph are not forced onto entities.

**URI-fallback = substrate debt (r3):** on substrate-derived artifacts, a URI/file-locator fallback is a **named deferral**, not an exemption — list it in DISPOSITIONS (or an equivalent greppable field) so the pipeline gap detector can mint graph debt. The drafting seat names the fallback; the detector (not the drafting seat) owns minting/resolving that debt.

## Omit-reason taxonomy

`reason(omit(C)) ∈ {fact_ceiling, source_class_insufficient, contradicted_or_unverified, operator_bind, scope_out}` + cite (ceiling row, source, assertion/entity id, bind, scope line).

`craft_only_reason valid ⇔ ¬∃ provenance_account(C) ∧ ¬strategy_material(C)` — "flow" cannot retire a claim that a fact ceiling, source, or operator bind speaks to.

## Citation gate — new authority claims

`add(authority_claim) ⇒ search_primaries first` — statute, case, standard, spec section, API contract, benchmark, version, price, verbatim quote.

Found ⇒ cite the primary. Empty ⇒ `[unverified: …]` ∨ `omit_with_reason`. `¬ invent from training memory`. `derivative_summary ≠ primary` — ingest the authoritative source before cite-level confidence.

## Evidence-class honesty (compatibility relation)

The table is the **compatibility relation** between epistemic class and permitted rendering — not a separate policy track.

| Class | Permitted |
|---|---|
| source_verified — primary bytes, entity URI, quoted artifact | state as fact + cite |
| operator/user statement | attribute ("operator reports X") until independently confirmed |
| tool_silent | `absence(evidence) ≠ evidence(absence)` — escalate the instrument (re-extract, vision pass, second tool) or mark; never "X did not happen" from a silent extractor |
| inference | flag as inference (`no-silent-inference` language) |

`class_upgrade requires new_evidence, ¬ rewording`. When a label or chronology proves wrong: supersede visibly; `¬ quietly rewrite`.

## Independence — load-bearing claims

- The author must not pre-select which claims get checked: decompose **all** material claims (at counterparty/filed tier: **all** claims, not only those the floor would have selected), then verify to the depth the floor allows.
- Load-bearing verbatim/figures: prefer non-author verification against the primary, blind to the draft's framing. `same_family(author, verifier) ⇒ partial_independence` — disclose it.
- One judgment dimension per verify pass: factual accuracy and relevance/specificity are separate questions.
- `∃ deterministic_check (ref resolves, number matches source, citation present) ⇒ run it`; `¬` ask a model to affirm what a check can prove. Check = judge; model = actuator of repairs.

## Repair, not cascade

`fails_verification(C) ⇒ targeted_repair(C)` — fix it, weaken it to permitted language, or omit it with reason. `¬ wipe siblings, ¬ delete the section, ¬ "clean up the paragraph"`.

After any mutation pass, re-audit the changed spans: every material claim present before and absent after must have a DISPOSITIONS line, or the pass failed.

## Ship gate (whole-record compatibility)

`ship(artifact) ⇒ ∀ load_bearing C: resolved(C) ∨ operator_waiver naming C`

This is the **whole-record compatibility check**: every material claim's rendering must be compatible with its epistemic state. resolved = backed ∨ downgraded to permitted language ∨ omitted with reason. Stripping a marker without resolving it is a silent upgrade ⇒ fail. Stacked polish/audit passes do not substitute for provenance resolution. Inaccurate-but-shipped is never the trade for late.

## Portable prompt block

For dispatch packets and seats without this skill — copy verbatim:

```text
PROVENANCE CONTRACT (writing-with-provenance)
You are rendering a claim record; keep the record true and the rendering no
stronger than the record supports.
1. Decompose your draft into material claims. Each gets exactly one disposition:
   express, imply, or omit_with_reason.
2. Back every expressed material claim with a source ref, or mark it inline as
   [unverified: <what>]. There is no third, silent option.
3. Before adding any authority claim (statute/case/standard/spec/benchmark/version/quote),
   search the primaries you were given. Not found => mark [unverified] or omit with reason.
   Never cite from memory.
4. Evidence classes: source-verified => state as fact; user/operator-said => attribute;
   tool-silent => not disproof, say so or escalate; inference => label as inference.
5. If a claim fails checking, repair that claim only. Do not delete siblings or
   "clean" the section.
6. Any claim you removed or weakened: list it under DISPOSITIONS with a reason,
   after the artifact.
7. Do not deliver with unresolved [unverified] markers on load-bearing claims:
   resolve, downgrade, omit with reason, or name them to the dispatcher.
```

## Compose — boundaries (do not re-encode)

| Skill | Owns |
|---|---|
| `no-silent-inference` | presence at insertion: verify-or-mark, classification gate, ask-vs-label |
| `external-prose-decompose-recompose` | rewrite-arc process: claim-ledger fields, earn test, modes, recompose-from-outline. The disposition law itself is THIS skill |
| `cortex-provenance-discipline` | Cortex-substrate citation grammar, permitted language, reader defense |
| `completion-provenance-discipline` | done/action claims bound to observed tool payloads |
| `evidence-review-discipline` | interpreting evidence artifacts beyond literal transcription (upstream of the class table) |
| `named-entity-verification-gate` | counterparty pre-ship named-entity check (composes at the ship gate) |

## Anti-patterns

Fault classes (type frictions with these): **desync** (rendering changed, record not) · **compatibility** (rendered stronger than epistemic state) · **illegal transition** (epistemic upgrade without evidence) · **category error** (mistaking one axis for the other).

| ✗ | ✓ | Fault |
|---|---|---|
| "Tightened the section" and claims vanished | DISPOSITIONS line per removed material claim | desync |
| Cite recalled from training memory | citation gate: primary search or `[unverified]` | compatibility |
| "The extractor found no X ⇒ no X happened" | tool-silent: escalate or mark | illegal transition |
| One failed claim ⇒ section deleted | proposition-scoped repair | category error |
| Omit reason "flow"/"space" while a ceiling/strategy row exists | taxonomy reason + cite | category error |
| Markers stripped at final polish | ship gate: resolve or waive by name | compatibility |
| Every candidate crammed in to avoid writing reasons | disposition ≠ inclusion mandate; imply/omit are first-class | category error |

## Minimal operating summary

Decompose to material claims (total at counterparty/filed). Disposition each; never drop silently. Back what you state or mark it. Gate new cites on primary search. Honor evidence classes as the compatibility relation; never upgrade by rewording. Verify load-bearing claims independently, one dimension at a time, deterministically where possible. Repair the failing claim, not its siblings. Ship only when the whole record is compatibility-clean — every load-bearing gap resolved or waived by name.
