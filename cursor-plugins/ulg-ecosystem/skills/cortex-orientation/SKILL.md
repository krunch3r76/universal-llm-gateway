---
related_skills: ["subgraph-render", "cortex-provenance-discipline"]
trigger_match_terms: ["cortex-orientation", "cortex_orientation", "cortex", "call", "boot", "close", "cortex-planning", "calling", "convention", "entity status", "summary_row", "withheld_newer", "myelinate", "myelination"]
description: "Before any cortex(...) call, session boot/close, or an entity status question — calling convention, read status from attributes + newest claims (not summary hints), assert gates; depth in reference.md."
---

## Trigger

Load before any `cortex(...)` call, `cortex_brief` on session boot or close, or when the ask is **current status/state** of a known entity (`account:`, `case:`, `decision:`, …).

## Refuse

- Quote `summary_row`, `predicate_summary`, `status_summary`, or `current_status.served` as **authoritative** current status (hints only; often stale).
- State entity status without reading the card (`entity_get`, `intent=card`) or without citing a backing assertion / attribute.
- Run Cortex entity/assertion/note **writes** from lane B, commit, or land — live op only (see [reference.md](reference.md)).

## Steps

1. **Calling convention.** `cortex(tool=<op>, arguments='<json string>')`. Cursor: `CallMcpTool(server="vortex-code", toolName="cortex", …)`. Boot: `cortex_brief(family, platform)`; hold `session_id` for writes. Non-trivial op catalog: `agent_skill:cortex`.

2. **Card read.** `cortex(tool="entity_get", arguments='{"entity_id":"<type:slug>","intent":"card"}')`.

3. **Status questions (interim rule).** Synthesize status from:
   - `attributes` (machine fields, e.g. `application_submitted`, `rap_application_status`, receipt URIs);
   - `top_k_assertions` (rank-ordered; not exhaustive) / newest claims by `observed_at`;
   - `current_status.withheld_newer[]` (list capped; newer states withheld from `served`).
   When `withheld_count` ≥ len(`withheld_newer`) shown, or the decisive claim may sit outside `top_k`, fetch assertions for the entity directly — do not trust the card slice alone.
   `withheld_newer` entries with non-null `reason` (e.g. `invention`) are **leads**, not status. Before citing a claim with `epistemic_state=flagged`, corroborate via `attributes` or an evidence/receipt URI on the entity.
   Treat `summary_row`, `predicate_summary`, `status_summary`, and `current_status.served` as **orientation hints** — scan for contradiction, never quote as final status when attributes or `withheld_newer` disagree. Cite assertion ids (e.g. `a:41768`) and dates.

4. **Before `assert`.** Stranger + negation + subject-centered gates; channel ladder (assert vs commit vs journal). Under-assert bias. Preflight: `dry_run=true`. Full tables: [reference.md](reference.md).

5. **Depth.** Ops, claim shape, `terminal_facts`, `recall`, myelinate: [reference.md](reference.md). Historical regression specimen **F1** (as of 2026-11-07): same file § F1. Provenance: `cortex-provenance-discipline`.

## Falsifier

The skill was not followed if the seat answers a status question from `summary_row` / `predicate_summary` / `status_summary` / `served` while newer `attributes` or `withheld_newer` contradict them, asserts status without `entity_get` evidence, or cites flagged/withheld-lead rows without corroboration. Regression specimen F1 must cite `a:41768`, not `summary_row` (detail in reference.md § F1).
