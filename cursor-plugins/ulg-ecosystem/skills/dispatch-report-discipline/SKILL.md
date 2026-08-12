---
name: dispatch-report-discipline
description: "Dispatch closeout reporting — eleven rules for truthful executor §2 closeouts; judgment rules 9–10 for minds only. Referenced by cursor-auto REPORTING CONTRACT block."
---

# Dispatch Report Discipline

Standing doctrine for what a truthful nested cursor-sdk closeout must contain.
The cursor-auto `build_sdk_message` injects a compressed **REPORTING CONTRACT**
block on every contract; this skill carries the full rules and reasoning.

## The eleven rules

### 1. SUFFICIENCY

Do enough to answer the question actually asked. Stopping at a subset is
permitted; reporting a subset as the whole is not.

**Why:** Partial work with honest scope delta is auditable; silent truncation is not.

**Enforcement:** L1 relay clamp + L2 prompt.

### 2. NEGATIVE ANSWERS ARE FIRST-CLASS

"Not found", "cannot access", "not achievable from this seat" are correct
complete responses — never traded for a plausible positive.

**Why:** Capability-blind callers cannot distinguish confident fiction from fact.

**Enforcement:** L2 prompt (machine cannot judge intent).

### 3. NO SILENT SUBSTITUTION

Model, scope, tool, or method differing from the request must appear in the
**returned artifact**, not only a receipt field.

**Why:** Callers read closeout bodies; receipt metadata is easy to miss.

**Enforcement:** L2 prompt + relay `MODEL ACTUAL` stamp when resolved ≠ requested.

### 4. SCOPE DELTA ON EVERY CLOSEOUT

Name what was and was not done. A closeout that cannot parse its own scope delta
must not report complete.

**Why:** Semantic shortfall is invisible at file-touch granularity (Gate D).

**Enforcement:** L1 clamp on missing `deltas_to_spec` / SCOPE DELTA + L2 prompt.

### 5. VERBATIM FOR EVIDENCE

Never paraphrase evidence — paraphrase launders interpretation into evidence.

**Why:** Relay and blind callers cannot re-open the source to catch drift.

**Enforcement:** L2 prompt; presence checkable, fidelity is judgment.

### 6. STATE COVERAGE BOUNDS

On every retrieval: corpus, count, actual date/ID range. A negative without
bounds is uninterpretable.

**Why:** "Nothing found" without bounds could mean empty corpus or wrong filter.

**Enforcement:** L2 prompt + relay presence check on COVERAGE field.

### 7. SURFACE CONTRADICTING EVIDENCE

Do not merely answer the literal question when the corpus contains material
that contradicts or qualifies the answer.

**Why:** Literal compliance with a wrong framing is still misreporting.

**Enforcement:** L2 prompt.

### 8. DISTINGUISH ABSENT FROM NOT-RETRIEVED

Report access status separately from result status.

**Why:** Absent data and unreachable data require different operator follow-ups.

**Enforcement:** L2 prompt + L1 false-absence guard on relay cells.

### 9. REVISION PASSES MUST NOT UNDO DELIBERATE PRIOR EDITS

Expansion or restoration of cut material is a regression until proven otherwise.

**Why:** Judgment about whether a prior edit was deliberate cannot be regex-checked.

**Enforcement:** **This skill only** — not wired to any gate.

### 10. ON OPERATOR-SIGNED PROSE PRESERVE VOICE AND REGISTER

Active/passive, person, punctuation habits — preserve the operator's voice when
editing their prose.

**Why:** Voice fidelity is aesthetic and contextual; a green regex check would lie.

**Enforcement:** **This skill only** — not wired to any gate.

### 11. READ-ONLY TASKS STAY READ-ONLY

Investigate/confer/verify episodes must not mutate scope unless explicitly
commissioned to write.

**Why:** Read-only contracts exist to bound blast radius.

**Enforcement:** L1 clamp + L2 prompt.

## Anti-patterns

| Bad | Good |
|---|---|
| Paraphrase a tool payload into "evidence" | Quote verbatim; note interpretation separately |
| Report `complete` with empty SCOPE DELTA | State what was skipped; status `partial` if material |
| Put model substitution only in dispatch receipt | **MODEL ACTUAL** in returned artifact body |
| Encode rule 9 or 10 as a regex gate | Apply by judgment; cite this skill |
| Hardcode `is_life_to_code` for tiering | `caller_auditable(from_agent=…)` deny-by-default allowlist |
| Allowlist grows by lane nickname | Allowlist entries name re-observability, not correlate labels |
| Long reporting block executor will skim | Terse checklist + skill reference for depth |

## Claim register on status language (member 6)

Status / rank / liveness / next-step claims in closeout relays are `observed`
only when quoting a substrate payload. Positional implication from a rank line,
ordinal adjacency, or "next open after…" is `derived` — ¬ render as observed.
SOT: `completion-provenance-discipline` §7. cursor-auto REPORTING CONTRACT
carries the one-liner; this skill owns judgment depth.

## Closeout git planes (a:28271 / closeout-plane-legibility)

Work status has **three** git planes, not two. Any rule naming only two
reproduces the unlabeled-provenance defect:

| Plane | Token | Meaning |
|---|---|---|
| Capture tip exists | `tip@lane-B(<ref>)` | Object in ODB / Lane-B branch tip (not authorship) |
| Local master land | `landed@local-master` / `NOT landed@local-master` | Ancestor of local `refs/heads/master` |
| Origin publish | `published@origin` / `NOT published@origin` | Ancestor of local `refs/remotes/origin/master` (no fetch) |

Closeouts carry an always-present `plane:` headline (grep-visible stranding —
no cross-field join) plus `@plane` qualifiers on `checkpoint:` /
`deployment_state:`. Degraded capture → `unknown@lane-B (capture head absent)` —
never upgrade unknown to a positive plane. **Lane-B land discipline
(todo:lane-b-land-discipline-harvest):** when `lane: B` ∧ `commits_ahead ≥ 1` ∧
`landed=false`, harvest is **incomplete** (`status: partial` +
`land:lane_b_unlanded`) until FF-merge/content-land or explicit discard — do not
treat deliberate-unlanded Lane-B as `complete`. Other planes/lanes: `status:
complete` may still be independent of origin publish.

## Composes with

- `completion-provenance-discipline` — tool-response binding + status/rank register (§7)
- `cdp-operator-proxy` — operator §2 CLOSEOUT template; mission-close inv for rank acts
- cursor-auto `reporting_contract.py` — injected L2 block (includes register one-liner)
- `caller_auditable.py` — tiered relay enforcement on missing checklist fields
