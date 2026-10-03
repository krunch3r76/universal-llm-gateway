---
name: conductor
description: "cursor-sdk mission operator: admit a conductor packet, drive G-rows via nested legs, cost-aware tier."
lifecycle: active
skill_category: orchestration
trigger_match_terms:
  - conductor
  - /conductor
  - follow up
  - page me when done
  - I'm leaving
related_skills:
  - orchestration-lanes
  - mission-operator
  - checkpoint-discipline
  - handoff-packet-authoring
  - consult-routing
  - bind-then-compose-dispatch
  - reasoning-posture
  - pager-notify
  - ulg-for-llms
  - judgment-escalation-ladder
  - life-operator-do-chain
---

# Conductor — cursor-sdk

`reference-*.md` = full text beside this file; install + HOME copy. Read per step; Customize: kernel + falsifiers.

## Before acting (kernel)

- **Run, don't ask; land on green.** Admitted ⇒ drive all open G-rows in one commission; packet = standing merge ack — land at tests+AC, ¬ second "ok to merge?" → `reference-run-to-completion.md`.
- **ROW_HOP vs exit-and-persist.** `ROW_HOP` = journal → hop CHECKPOINT → closeout `stop: ROW_HOP`; substrate admits successor — never `team_dispatch(reuse_thread=<own thread>)`. `ROW_PINNED` · `HOLD_MERGE` · `OPERATOR_GATE` · `PARKED_TRANSPORT` · `DONE` persist and stop past the unpaid gate → `reference-invariant.md`, `reference-packet.md`.
- **Nested CDP (a:37183).** Before `team_dispatch(model=cdp/…)` **or** `cse_session(op=followup)` on G1·G2·G4·G6/width: Use `retrieval-before-authoring`, write report sidecar, `target:` bound to prompt, `retrieval_report: cortex://…` own line (line-anchored). G4/SKEPTIC/adversarial-spec ¬ delivery/code-review chrome. Hop closeout: every nested prompt URI + report bundle — missing ⇒ not green.
- **Review harvest ≺ land ≺ DONE.** Land/`DONE`: harvest stronger-model review first — ¬ background, ¬ land-then-review → `reference-run-to-completion.md`, `reference-packet.md`.
- **G5 witness (a:37198).** After G3→G5 CDP score-ratify harvest, required attended witness = `SCORE_RESURFACE` on `summoning_thread_id` (slug subject; CDP exec/review sha) — ¬ tip `DONE`, ¬ steer-inject, ¬ harvest alone. Attended G5 fold needs that turn **and** implement (`ledger:nested_implement` ∨ `git:lane_head`). `bus:SCORE_RESURFACE` ⇒ ¬ re-post · ¬ re-ratify → `reference-packet.md`.
- **Explicit `lane="B"`.** Top-level admit passes `lane="B"`; omit nest/resume only — GIW default Lane A, not "no preference" → `reference-admit.md`, `reference-gotchas.md`.

## Trigger

Author/admit packet, drive G-rows, `/conductor`, or `follow up` / `page me when done` / `I'm leaving`.

## Refuse

- Drop or rewrite a rule when moving it between this index and a reference file.

## Steps

1. **Duty and audience.** → `reference-overview.md`, `reference-audience.md`.
   Falsifier: admit lacks `Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost tier from this skill.` in `<invariants>`.
   Specimen: Gate-2 slug `conductor`.

2. **Invariant + RTC.** → `reference-invariant.md`, `reference-run-to-completion.md`.
   Falsifier: failed CDP consult waits, retries pool, or DEFERRED while lane continues.
   Specimen: `cdp_fail_route` `nested-grok` and `operator`.

3. **When, tier, roles.** → `reference-when.md`, `reference-model-tier.md`, `reference-role-split.md`.
   Falsifier: refused effort pin swapped to another rung, or conductor writes a mechanical G-row.
   Specimen: S7-I.

4. **Packet through resume.** → `reference-packet.md`.
   Falsifier: `team_dispatch(reuse_thread=<own thread>)` from live seat.
   Specimen: `422 CURSOR_WORKER_THREAD_OCCUPIED`.

5. **Plan, admit, close.** → `reference-admit-from-plan.md`, `reference-admit.md`, `reference-gotchas.md`, `reference-interactive-and-anti-patterns.md`.
   Falsifier: `lane=` omitted after `CURSOR_LANE_B_SCOPE_REFUSED`, or DONE without quoted commit + recycle/skip.
   Specimen: 7281 / 7286, 2026-08-15.

## Falsifier

Failed if a kernel rule is missing, or a reference file is absent from plugin SoT beside `SKILL.md` while install expects split body.
