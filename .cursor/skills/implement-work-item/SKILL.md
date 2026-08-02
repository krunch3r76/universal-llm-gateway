---
name: implement-work-item
description: "When executing any code-touching work item from todo, plan phase, or task — pickup discipline, scope gates, service running, and structured implement report."
---

# Implement Work Item — Execute and Report

**Version:** 1.2 · **Authority:** HIGH for code modification + running service + structured report.

## Trigger / exclusions

Read when executing any `todo:` / `task:` / `plan_phase:` / execution-shaped entity satisfying all:

```text
code_modifying ∧ service_touching ∧ report_required ⇒ load(this_skill)
```

Entity type is not the trigger. Do NOT read for pure Cortex data writes, read-only verifier sessions, or plan deck authoring (`implementation-plan-workflow`).

Companions: `implement-todo` (readiness/routing), `implementation-plan-workflow` (deck/coordinator), `completion-provenance-discipline` (done-claim evidence), `ulg-architecture` `[ulg:service-ops]` (quality_gate → sync_restart → wait_healthy), `architecture-invariants` `[quality]/[scope]/[simplicity]`.

## Core rule

```text
done_claim ⊢ raw_tool_response ∧ ¬prose_assertion
gate_result=FAIL ⇒ STOP ∧ ¬write_summary ∧ ¬close ∧ report_failure
```

Every checklist item binds to observed tool payload, not expectation/inference/source-code shape. If gate or live probe fails/unexpected, stop; do not assert success, close work item, or advance workflow state.

## Protocol

### 1. Pre-task reads

Before first write:
1. Read work item in full (`entity_get` for entities; `fs(read)` for phase docs). Confirm scope, tasks, verification checklist.
2. Load governing skills per `implement-todo §1b` (required_skills + requires edges; ULG repo ⇒ architecture floor).
3. Read every source file immediately before editing it.

```text
¬read(source_file) ⇒ ¬edit(source_file)
```

Earlier session read is insufficient after intervening edits; re-read before edit. Run any mandatory pre-task step and bind outputs before editing.

### 2. Dependency gate

If work item declares `Depends-on`, verify both from live payloads:
1. prerequisite entity `workflow_state=done` via `entity_get(..., intent=card)`;
2. plan-phase summary file exists and contains `Verification result: PASS`.

```text
dep_gate_fail = entity.workflow_state≠done ∨ summary_missing ∨ ¬summary.shows_PASS
dep_gate_fail ⇒ STOP ∧ report_failed_condition
```

Do not infer dependency satisfaction from prior claims.

### 3. Code edits

For each change:
- `[scope]`: every changed line traces to task/direct consequence; no opportunistic refactor/cleanup.
- `[simplicity]`: minimum root-cause fix.
- Use atomic replace/write ops; read back affected region immediately.
- Capture every `written_sha256`; this is edit evidence.
- BEFORE/AFTER doc present ⇒ live BEFORE must match before applying AFTER. If divergent, surface; do not force-apply.

### 4. Post-code loop for running service

Exact order; never restart before gate passes.

A. **Quality gate**

```text
quality_gate(files=[edited files])
```

Bind raw result `{passed, ruff, compile, imports, tests}`. Required: `passed:true` and all sub-gates pass. Lane A pytest under `libs/llm_adapters/` or `libs/model_id/` counts as pass criterion.

```text
quality_gate_fail ⇒ STOP ∧ ¬restart ∧ report_failures_verbatim
```

B. **Restart** — requires operator approval immediately before running.

```text
manage(action="sync_restart", service=<service>)
manage(action="wait_healthy", service=<service>)
```

Bind old PID→new PID and `waited_s`.

C. **Live probe**

Run work-item verification probe; paste raw payload. Bind probe-specific fields, e.g.:

| Probe | Cite fields |
|---|---|
| `/skills/body` | `source_uri`, digest, non-empty body |
| service health | `status`, `pid`, `version` |
| quality gate | `passed`, subresults, test count/time |

Probe failure/unexpected ⇒ STOP. Quality gate is necessary, not sufficient.

### 5. Work item report

First line MUST be:

```text
**Status: PASS | INCOMPLETE | FAIL**
```

- PASS: all required tasks performed this session; all gates/probes passed.
- INCOMPLETE: required task not performed this session.
- FAIL: attempted task/gate/probe/write returned error.

Report shape:

```markdown
## Work Item Report

**Status: [PASS | INCOMPLETE | FAIL]**

### Dependency gate
- <condition>: <observed value + response source>

### What was done
- <task>: <change> — written_sha256=<hash>

### Verification checklist
| Check | Evidence |
|---|---|
| Quality gate N/N | ruff: ... compile: ... tests: ... |
| sync_restart <service> | PID old → new |
| wait_healthy | waited_s: N |
| Live probe: <name> | raw key fields |
| <work-item checklist item> | raw tool-response field |

### Durable state seeded
<files+sha256; entity updates+workflow_state; assertion IDs>

### Flagged
<unexpected findings/deviations/side findings or "none">
```

Checklist evidence must be falsifiable from transcript. “Passed”/“healthy” without raw fields is noncompliant.

### 6. Close out — PASS only

Execute only after live probe returns expected results.

Plan phases: write `summaries/phase-N-summary.md`; set `plan_phase:{plan}/phase-N` done; final phase writes `summaries/00-{arc}-wrap-up.md` and sets `plan:{plan}` done.

Todos/tasks: set entity `workflow_state=done`; seed assertions with `evidence_uris` pointing to completed work.

```text
any §4 gate fail ∨ live_probe_not_run ⇒ ¬close ∧ ¬write_summary
```

## INCOMPLETE state

```text
task ∉ session_writes ⇒ ¬claim_done(task)
```

A task is done only when this session produced the write hash or tool payload. Finding code/data already in expected shape does not satisfy the task.

FAIL = attempted and gate/probe/write errored. INCOMPLETE = not performed (skipped/deferred/pre-existing).

Pre-existing completion trap response:
1. State observed pre-existing state and timestamp/session if knowable.
2. Do NOT claim task was done.
3. Declare `Status: INCOMPLETE` with performed vs not performed breakdown.
4. Do NOT close entity or write passing summary.
5. Surface remaining unperformed tasks and why.

Front-load INCOMPLETE; operator must see it before reassuring context.

```text
Status: INCOMPLETE
Tasks not performed by this session:
- Task 1: found already present; this session did not write it.
Tasks performed by this session:
- ...
Recommendation: verify which session made pre-existing changes and correctness.
```

## Anti-patterns

| Anti-pattern | Correct behavior |
|---|---|
| “Quality gate passed” without N/N/raw fields | paste raw gate output |
| Restart before quality gate | gate first |
| Probe summary without raw response | paste raw response/key fields |
| Summary before live probe | probe first; close only on PASS |
| Skipping dependency gate | run entity + summary checks |
| Reusing stale source read | re-read immediately before edit |
| Sub-gate failed but done claimed | STOP/report failure |
| Pre-existing work → PASS | INCOMPLETE; this session did not perform task |
| Status line omitted/late | first line = Status |

## Provenance

Authored from sf1-close-authoritative-uri arc. Boundary: `implement-todo` owns readiness/routing; `implementation-plan-workflow` owns deck/coordinator; this skill owns execution from “proceed” to evidence-backed close.
