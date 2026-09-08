---
name: implement-work-item
description: "When executing any code-touching work item from todo, plan phase, or task — pickup discipline, scope gates, service running, and structured implement report."
trigger_match_terms: ["code-touching", "discipline", "executing", "implement-work-item", "implement_work_item", "item", "phase", "pickup", "plan", "scope", "todo", "work", "sdk_mode", "plan:closeout_verdict"]
generator_version: "1.0.0"
---

# Implement Work Item

Code-touching pickup for `todo:`, `plan_phase:`, and task entities — after
G-ladder densify or a direct implement dispatch.

## Trigger / boundary

Fire on implement-class `team_dispatch`, `/todo pickup` when route is DISPATCH,
or conductor G5 nest with `contract=implement`.

`is_plan_arc(todo) ⇒ load(implementation-plan-workflow)` for multi-phase deck
coordination. This skill covers **single implement leg** pickup.

Companion: `implement-todo` (todo readiness + Gate-6 stack), `consult-routing`
(implement admission), `dispatch-workflow` (transport shape).

## Core invariant

`implement_pickup ⇒ verify_live ∧ load_governing_skills ∧ plan_leg_harvest? ∧ gauge_readiness ∧ execute`.

A prior **plan** leg is not optional context — read its verdict and artifacts
before touching repo files.

## Plan-leg pickup (BINDING — prior leg was `sdk_mode=plan`)

When the work thread or scoreboard shows a terminal plan dispatch on this
`work_key`, or the implement packet cites `nest_under=<plan_dispatch_id>`:

1. **Read plan closeout** on the worker thread (or `substrate_bus_tip` + `get`).
   Quote `plan:closeout_verdict` from deviations — expect `PLAN_COMPLETE` or
   `PARTIAL` (not `COMPLETE` / not land claims).
2. **Harvest plan artifacts** — every URI in plan closeout `artifact_paths` /
   sidecar `<corpus>`; load into working context before scope bind.
3. **Verify implement gate** — `density_triage: implement_ready` (or mechanical
   with dense `files_expected`) stamped on the todo; plan leg alone does not
   satisfy implement admission.
4. **Nest shape** — implement admit uses `nest_under=<plan_dispatch_id>`,
   `sdk_mode: agent` (or omit — implement-class defaults agent). **`sdk_mode=plan`
   on `contract=implement` → 422** (`validate_sdk_mode_at_admit`).
5. **W3 nest hint** — `cursor-auto` nested `ask|recon|seed` without
   `implement_ready` auto-stamps plan via `nested_auto_sdk_mode`; the conductor
   (or lead) still fires the explicit implement nest after `PLAN_COMPLETE`.

| Prior verdict | Implement pickup |
|---|---|
| `PLAN_COMPLETE` + artifact URIs | Proceed — bind plan spec into implement packet `<corpus>` |
| `PARTIAL` | Proceed only if packet names which open forks block implement; else halt |
| Missing / plan claimed land | **Halt** — plan forbids land; re-run plan or fix closeout |
| No plan leg, direct implement | Skip § Plan-leg pickup |

SoT: `cursor_sdk_mode.py` · `docs/agent-guides/cursor-sdk-conversation-mode.md`.

## sdk_mode decision (implement pickup)

| Situation | `sdk_mode` | `contract` |
|---|---|---|
| Executing code after plan bind | **`agent`** (default) | `implement` \| `pure-mechanical` |
| Sparse recon still owed | **`plan`** | `none` \| `recon` \| `seed` \| `consult` — **not** this skill's implement path |
| Conductor orchestrating | **`agent`** | `conductor` |

## Protocol (implement leg)

### 1. Verify live

`entity_get(todo|plan_phase, intent="full")`; confirm `workflow_state ∈ {open,in_progress}`.

### 2. Load governing skills

Same floor as `implement-todo` §1b — architecture-invariants, ulg-architecture,
docstring-quality, event-instrumentation-discipline when unset.

### 3. Readiness + route

Delegate Gate-2 / Gate-6 detail to `implement-todo` §2–3. Implement requires
`implement_ready` + `spec_sha256` unless `density_triage=mechanical`.

### 4. Execute + report

Lane B edits in worktree; restart discipline per touched services. Closeout:
path-explicit evidence, `land_disposition`, no plan-style artifact-only claims
on implement legs.

## Anti-patterns

| Bad | Good |
|---|---|
| Implement without reading `plan:closeout_verdict` | Harvest plan closeout + URIs first |
| `sdk_mode=plan` on implement dispatch | `agent` or omit on `contract=implement` |
| Treat plan land claims as shipped | Plan closeout strips land; implement owns merge |
| Skip `nest_under` after plan on same worktree | `nest_under=<plan_dispatch_id>` inherit lane |
