---
name: conductor
description: "On cursor-sdk as mission operator of a continuity root — author/admit a conductor packet, drive G-rows via nested legs, cost-aware model tier, interactive /conductor setup."
lifecycle: active
skill_category: orchestration
trigger_match_terms:
  - conductor
  - mission conductor
  - off-tick conductor
  - cursor-sdk conductor
  - conductor packet
  - conductor score
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

# Conductor — cursor-sdk as mission operator

Index and before-acting kernel. **SDK seats** (`stage_dispatch_skills`) receive **only this file** — act from the kernel below and the step falsifiers. Flat `reference-*.md` files beside this skill in plugin SoT carry the full master text where the install copies them (IDE checkout / local plugin tree); they are not mounted on SDK dispatch.

## Before acting (kernel)

- **Run, don't ask; land on green.** Once admitted, drive every open G-row in one standing commission; the packet is standing merge ack — land when tests and AC are met, not for a second "ok to merge?" (see `reference-run-to-completion.md`).
- **ROW_HOP vs exit-and-persist.** `ROW_HOP` = journal → hop CHECKPOINT → closeout `stop: ROW_HOP`; substrate admits the successor — never `team_dispatch(reuse_thread=<own thread>)`. `ROW_PINNED` · `HOLD_MERGE` · `OPERATOR_GATE` · `PARKED_TRANSPORT` · `DONE` persist and stop progression past the unpaid gate (see `reference-invariant.md`, `reference-packet.md`).
- **Review harvest ≺ land ≺ DONE.** On codework claiming land or terminal `DONE`, harvest the owed stronger-model review verdict before those claims — not background, not land-then-review (see `reference-run-to-completion.md`, `reference-packet.md`).
- **Explicit `lane="B"`.** Top-level conductor admit passes `lane="B"` explicitly; omit inherits on nest/resume only — default at GIW is Lane A, not "no preference" (see `reference-admit.md`, `reference-gotchas.md`).

## Trigger

Author or admit a conductor packet, drive G-rows on cursor-sdk, run `/conductor`, or answer `follow up`, `page me when done`, or `I'm leaving`.

## Refuse

- Drop or rewrite a rule while moving it between this index and a reference file.

## Steps

1. **Duty and audience.** Full text: `reference-overview.md`, `reference-audience.md`.
   Falsifier: a conductor admit lacks `Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost tier from this skill.` in `<invariants>`.
   Specimen: Gate-2 slug `conductor`.

2. **Invariant and run to completion.** Full text: `reference-invariant.md`, `reference-run-to-completion.md`.
   Falsifier: a failed CDP consult waits, retries the pool, or is labeled DEFERRED while the lane continues.
   Specimen: `cdp_fail_route` `nested-grok` and `operator`.

3. **When, tier, roles.** Full text: `reference-when.md`, `reference-model-tier.md`, `reference-role-split.md`.
   Falsifier: a refused effort pin is swapped to another rung, or the conductor writes a mechanical G-row.
   Specimen: S7-I.

4. **Packet through resume.** Full text: `reference-packet.md`.
   Falsifier: the live seat calls `team_dispatch(reuse_thread=<own thread>)`.
   Specimen: `422 CURSOR_WORKER_THREAD_OCCUPIED`.

5. **Plan, admit, gotchas, close.** Full text: `reference-admit-from-plan.md`, `reference-admit.md`, `reference-gotchas.md`, `reference-interactive-and-anti-patterns.md`.
   Falsifier: `lane=` is omitted after `CURSOR_LANE_B_SCOPE_REFUSED`, or DONE is claimed with no quoted commit and recycle or named skip.
   Specimen: failure class 7281 / 7286, 2026-08-15.

## Falsifier

This index failed to bind if an item-2 kernel rule is missing here, or a reference file is absent from plugin SoT beside `SKILL.md` while install still expects the split body.
