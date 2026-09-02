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
| Auto-fix tool (`ruff --fix`, `--unsafe-fixes`) silently mutates a file beyond the hand-edited diff; closeout reports `scope_deviations: none` | Name every auto-fix side-effect in SCOPE DELTA even when net-behavior-neutral — Rule 3/4 bind to *method*, not just outcome (agent-bus:9880 turn 24: `ruff check --fix` on a Phase-2 implement dispatch dropped a pre-existing unused import from a file the packet said would otherwise be untouched; behavior-neutral, unreported) |

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

### Branch discharge is a closeout requirement, not a grading footnote

`∀ Lane-B closeout: declare(land_disposition) ∨ own(branch_debt)`.

The grade above says what the harvest *is*; discharge says what happens to the
branch. Every Lane-B closeout carries one of two lines:

| Line | Meaning | Effect |
|---|---|---|
| `land_disposition: landed` | The work is on master | GIW **content-probes** it, archives the tip, deletes the branch |
| `land_disposition: discard` + `land_reason: <why>` | Deliberately abandoned | Archives the tip, deletes the branch, records the reason |

Omit the line while the branch carries commits master lacks and GIW opens an
attributed **branch debt** (`cursor_sdk_branch_debts`) naming your thread,
dispatch, and caller. It surfaces in `busy_status` / `lane_hygiene` and in the
admit response at this lane's *next* dispatch, escalates on the owning bus
thread once aged, and refuses that lane's Lane-B admit at the hard horizon.

`landed` is **measured, not asserted** — a claim the tree does not support is
refused, names the paths that disagree, and opens the debt anyway. A reasoned
`discard` is a complete honest outcome; a false `landed` is not. Discharge later
via `POST /cursor-sdk/branch-discharge`; nothing is deleted unarchived, so
neither exit loses work.

**Harvest briefing one-liner:** when relaying a Lane-B closeout, name
``branch=<lane-branch> head=<head_sha>`` and whether land is still owed
(``land owed`` when `landed=false` ∧ `commits_ahead≥1`, else ``landed`` or
``discarded`` after discharge).

### Status claim×measure polarity (arc 6655 / P1)

Polysemous ``partial`` splits into ``partial:work`` vs ``partial:capture`` on
envelope measurement (sibling ``status_incomplete_class:`` when prose). Readers
must consult ``deviations[]`` / ``work_outcome`` / ``capture_status`` before
ascribing seat-lie on ``complete×partial`` — that cell emits ``plane-legend:``,
not uniform ``plane-discrepancy:``. Honesty ``partial×complete`` stays
``plane-register:``. Authority when claim≠measure (code, not prose):

| Question | Wins | Loses |
|---|---|---|
| work_outcome / machine grade | measure | claim |
| AC-pass | measure (`status@infra`) | claim |
| next-step | deviations-qualified measure | bare claim; bare measure token |

### Verification array semantics — worst-of, not last-wins (a:29677)

`verification[]` is **append-only**: reruns add rows; they do not replace earlier
rows. Machine grading (`resolve_work_outcome`, `verification_all_pass`,
`verification_has_failure` in `cursor_sdk_capture_status.py`) evaluates the
**entire** array — **worst-of / all-rows**, not first-row and not last-row.

| Semantics | Meaning |
|---|---|
| Any `row_is_failed_check` row | Can mint `work_outcome=checks_failed` when positive deliverable evidence exists |
| `verification_all_pass` | Requires every row to clear — one blocking row blocks `shipped` |
| Later passing rerun | Does **not** erase an earlier failing row's effect on `status` / `work_outcome` |

This is deliberate fail-closed design (`todo:closeout-grade-trust-join`, arc 7190):
a rerun-until-green pattern is indistinguishable at the row level from a genuine
fix, so the closeout refuses to claim `shipped` when the full evidence includes
a failed check — even if a later row passed.

**Reader rule:** trust top-level `status` / `work_outcome` as authoritative machine
grade. Do not substitute "did the last verification row pass?" for the graded
outcome. When the full array matters, read every row — not only the final one.

## Composes with

- `completion-provenance-discipline` — tool-response binding + status/rank register (§7)
- `cdp-operator-proxy` — operator §2 CLOSEOUT template; mission-close inv for rank acts
- cursor-auto `reporting_contract.py` — injected L2 block (includes register one-liner)
- `caller_auditable.py` — tiered relay enforcement on missing checklist fields
