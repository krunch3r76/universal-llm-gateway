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

Kernel bullets (run/land, ROW_HOP, width-seat critic `cdp/opus-5.5` (`impresario.critic`), review≺land, G5 witness, lane=B) → `reference-overview.md` § Before acting (kernel).

## Guardrails

- **Fleet auth stays on MCP tools.** Never read `MCP_AUTH_TOKEN` or any `*_TOKEN` / credential env var in the agent shell. Never call `team_dispatch`, registry, `agent_bus`, or other fleet HTTP endpoints via raw `curl`, `httpx`, or hand-rolled HTTP — use the vortex MCP tools only.

## Trigger

Author/admit packet, drive G-rows, `/conductor`, or `follow up` / `page me when done` / `I'm leaving`.

## Refuse

- Drop or rewrite a rule when moving it between this index and a reference file.

## Steps

1. **Duty and audience.** → `reference-overview.md`, `reference-audience.md`.
   Falsifier: admit lacks `Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost tier from this skill.` in `<invariants>`.
   Specimen: Gate-2 slug `conductor`.

2. **Invariant + RTC.** → `reference-invariant.md`, `reference-run-to-completion.md`.
   Falsifier: failed CDP consult waits, retries pool, or DEFERRED while lane continues; nested G6 holds MCP wait until -32001 or ROW_HOPs while that execution_id is still live.
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

- **In-flight guard.** Table → `reference-in-flight-guard.md`.
