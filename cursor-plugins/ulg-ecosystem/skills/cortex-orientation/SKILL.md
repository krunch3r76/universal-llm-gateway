---
related_skills: ["subgraph-render", "cortex-provenance-discipline"]
trigger_match_terms: ["cortex-orientation", "cortex_orientation", "cortex", "call", "boot", "close", "cortex-planning", "calling", "convention", "entity status", "summary_row", "withheld_newer", "myelinate", "myelination"]
description: "Before any cortex(...) call, session boot/close, or an entity status question — calling convention, read status from attributes + newest claims (not summary hints), assert gates; depth in references/."
---

## Trigger

Load before any `cortex(...)` call, `cortex_brief` on session boot or close, or when the ask is **current status/state** of a known entity (`account:`, `case:`, `decision:`, …).

## Refuse

- Quote `summary_row`, `predicate_summary`, `status_summary`, or `current_status.served` as **authoritative** current status (hints only; often stale).
- State entity status without reading the card (`entity_get`, `intent=card`) or without citing a backing assertion / attribute.
- Run Cortex entity/assertion/note **writes** from lane B, commit, or land — live op only (see [details.md](references/details.md)).

## Steps

1. **Calling convention.** `cortex(tool=<op>, arguments='<json string>')`. Cursor: `CallMcpTool(server="vortex-code", toolName="cortex", …)`. Boot: `cortex_brief(family, platform)`; hold `session_id` for writes. Non-trivial op catalog: `agent_skill:cortex`.

2. **Card read.** `cortex(tool="entity_get", arguments='{"entity_id":"<type:slug>","intent":"card"}')`.

3. **Status questions (interim rule).** Synthesize status from:
   - `attributes` (machine fields, e.g. `application_submitted`, `rap_application_status`);
   - `top_k_assertions` / newest claims by `observed_at`;
   - `current_status.withheld_newer[]` (newer states withheld from `served`).
   Treat `summary_row`, `predicate_summary`, `status_summary`, and `current_status.served` as **orientation hints** — scan for contradiction, never quote as final status when attributes or `withheld_newer` disagree. Cite assertion ids (e.g. `a:41768`) and dates; check `epistemic_state` on flagged claims.

4. **Before `assert`.** Stranger + negation + subject-centered gates; channel ladder (assert vs commit vs journal). Under-assert bias. Preflight: `dry_run=true`. Full tables: [details.md](references/details.md).

5. **Depth.** Ops, claim shape, `terminal_facts`, `recall`, myelinate: [details.md](references/details.md). Provenance: `cortex-provenance-discipline`.

### Worked example (F1)

`entity_get` `account:edfinancial-student-loan-9158` card: `summary_row` and `predicate_summary` imply RAP **not submitted**; `current_status.served` is `a:40748` (2026-10-06 final notice). **Correct cold-seat answer:** RAP **submitted** 2026-11-03 (`a:41768`, `withheld_newer` state `submitted`); `attributes.application_submitted=true`, `attributes.rap_application_status=submitted_by_Kaywan_awaiting_servicer_processing` — awaiting servicer processing. **Wrong:** repeating "not submitted" from `summary_row` or `predicate_summary`.

## Falsifier

The skill was not followed if the seat answers a status question from `summary_row` / `predicate_summary` / `status_summary` / `served` while newer `attributes` or `withheld_newer` contradict them, or asserts status without `entity_get` evidence. Regression specimen: F1 must cite `a:41768`, not `summary_row`.
