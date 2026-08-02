---
name: implementation-plan-workflow
description: "Use when authoring a multi-phase implementation plan deck (/create-implementation-plan) — phase structure, gates, executor packets, and dispatch-ready task guidance."
trigger_match_terms: ["implementation-plan-workflow", "implementation_plan_workflow", "multi-phase", "implementation", "plan", "deck", "dispatch-delegation", "author", "create-implementation-plan", "shape", "non-cursor", "sea"]
---

# Implementation-Plan Workflow

**Version:** 1.5.0 · **Authority:** HIGH. Malformed phase decks cascade into executor failures and wasted dispatch budget.

## Trigger / exclusions

Read before:
- authoring/reviewing a multi-phase deck under `tmp/prompts/{name}/`;
- expanding a `todo:` into `/create-implementation-plan`-grade deck;
- coordinating an approved deck: dispatch phases, await summaries, stitch handoffs, final report;
- writing coordinator final report.

Do NOT read for: Cortex entity creation (`entity-lifecycle-discipline`), single bounded `todo:` pickup (`implement-todo`; it promotes to plan only if Todo→Plan thresholds fire), or pipeline mechanics inside a phase (`build-pipeline` owns mechanics; this owns phase-doc wrapper).

## Companion load

```text
composing_deck ⇒ load(frontier-model-instructions) ∧ load(this_skill)
phase_touches_workspace_code ⇒ load(architecture-invariants)
phase_touches_ULG ⇒ load(architecture-invariants) ∧ load(ulg-architecture)
```

Also load when relevant: `entity-lifecycle-discipline`, `implement-todo`, `dispatch-workflow`, `skill-document-writing`. Phase docs may point executors at workspace skills such as `.cursor/skills/build-pipeline/SKILL.md`.

## Core rule

```text
deck = deliverable ∧ phase_doc.precision ⇒ executor_zero_reading
parallel_group_dispatch = directed_graph_traversal
```

A phase doc must let a low-tier executor implement without grepping to discover what to change. Same `Parallel-group` letter dispatches simultaneously; next group starts only after every current-group summary lands. Dependencies must be known before execution.

## Modes

| Mode | Role | Output |
|---|---|---|
| **Author** | read packet/todo; produce `tmp/prompts/{name}/` deck | operator-reviewable deck |
| **Coordinate** | dispatch by parallel group; await summaries; stitch handoffs | landed implementation + closure report |

Web-claude can author, coordinate, and execute via `fs(workspaces)` + tests/probes. Composer/cursor-sdk is the default code harness.

## Plan-author pre-flight

Before README/phase docs, run live-code sweep:

1. **`libs/` adjacent-primitives sweep (mandatory when creating infrastructure).** Search for existing primitives/exceptions/observability/timeouts/cancellation semantics; list fits/near-misses in README Review findings. ULG starts at `ulg-architecture` Libs Inventory plus fresh `fs(list libs)`.
2. **In-pipeline precedent sweep.** Find sibling code paths in same subsystem; cite precedent files in phase docs.
3. **Open-question audit.** README Open questions ≤3 advisory. `>3` blocking ⇒ deck not plan-ready.

Cursor `/create-implementation-plan` has same rule; do not rely on eyeballed memory.

## Deck structure

```text
tmp/prompts/{name}/
  README.md
  phase-1-{short-slug}.md ... phase-N-{short-slug}.md
  summaries/README.md; phase-N-summary.md placeholders; 00-{arc}-wrap-up.md at close
  handoffs/ optional {N}-phase{N}-kickoff.md
```

Rules: `{name}` matches originating `todo:`/`plan:` slug; phase filenames include descriptive slug; README first; summaries dir exists author-time; handoffs only for non-Cursor/custom kickoff executors.

### README contract (ordered)

1. frontmatter: slug, primary entity, plan-context entity, owners, date, status;
2. manifest table: `# | Title | Group | Depends-on | Density | Executor`;
3. parallel-group dispatch order;
4. total estimated SLOC;
5. closure commit scope;
6. review findings table;
7. architectural decisions table (`Selected`, `Rejected`, `Rationale`);
8. event vocabulary (add/modify/none + rationale);
9. documentation impact;
10. non-goals;
11. open questions ≤3 advisory;
12. operator next step exact command/call;
13. deliverables paths + one-line description.

### Phase doc contract

Top fields: `Expected Executor`, `Executor Mode`, `Parallel-group`, `Depends-on`, `Optional Consultation`, `Suggested Reviewer`. `None` is a valid value; blank is not.

Mandatory sections: `## Prior Phase Inputs` iff dependency; `## Objective`; `## Pattern Identification`; `## Alternatives considered` for architecture choices; `## Tasks`; `## Verification`; `## Expected Files`; `## Event Vocabulary`; `## Cleanup`; `## Output for next phase`.

#### BEFORE/AFTER completeness

```text
∀ modify_task: complete BEFORE + complete AFTER ⇒ executor_zero_reading
∀ create_task: complete file, all imports/functions/docstrings; no ellipsis
```

BEFORE blocks include current code + 3–5 context lines; AFTER is full replacement, not diff fragment. No `# ... rest unchanged`. If complete file >300 SLOC, split phase.

#### Density → tier

| Density | Tier |
|---|---|
| sparse/architectural | Opus 4.8 Low thinking |
| dense/pseudocode/checklist | Sonnet 4.6 Medium thinking |
| exploration/investigation | Sonnet 4.6 High thinking |
| mechanical | Grok 4.20 or Sonnet Low non-thinking |

Mixed density ⇒ split before dispatch.

## Coordinator side

### Parallel safety

```text
parallel_safe(A,B) ⇔ ExpectedFiles(A) ∩ ExpectedFiles(B)=∅ ∧ ¬Depends-on(A,B) ∧ ¬Depends-on(B,A)
```

Overlap without dependency ⇒ blocker.

### Dispatch order

```text
topological_sort(phases) → partition by Parallel-group
∀ group: dispatch_all → await_all_summaries → handoff_payload → next_group
```

Do not dispatch next group until all current summaries land.

### Executor system_context floor

Inject this block into every executor dispatch as a floor:

```text
[logging]   ∀ application code: from universal_logging import get_logger; ¬ logging.getLogger()/import logging
[sloc]      new files ≤300 SLOC; existing files ≤400 SLOC
[sloc-gate] pre-edit: SLOC >350 ∧ adding >20 ⇒ split first
[signals]   signal format ^[a-z]+(\.[a-z]+){1,4}$; no _, -, digits
[scope]     every changed line traces to task; ¬ unrequested refactor/reformat
[srp]       ∀ fn: validation ∧ orchestration ∧ mutation ∧ I/O ⇒ split
```

Floor ≠ ceiling. For workspace code, instruct executor to read `architecture-invariants.md`; for ULG also `ulg-architecture.md` before first edit. Add phase doc and relevant workspace skills. Python-heavy phases add recurring friction checks below.

### Handoff payload

After group N:

```text
Phase N complete:
- Status
- Files modified
- Public API exposed
- Deviations
- Next-phase readiness
```

Downstream phases may assume payload delivered before dispatch. Final report derives file changes from summaries, not plan forecasts.

### Final report

Include phase summary table, per-phase file changes, union of all files changed, blockers/deviations. Paths come from phase summaries only.

## Cortex seeding

Summaries may rotate; Cortex graph endures. Each completed phase seeds/verifies:

1. `plan:{name}` (`workflow_state=in_progress`, done on final phase).
2. `plan_phase:{name}/phase-{N}` with attributes `{plan, phase, session_id, files_modified}` and `workflow_state=done`.
3. `relationship_create(source_id=plan_phase:{name}/phase-{N}, target_id=plan:{name}, type_id="child_of")` — NOT `contains`.

Coordinator writes `summaries/00-{arc}-wrap-up.md` at close: deliverables, phase ledger, architectural decisions, recurring friction, limitations, open items, operator checklist, continuity.

## Cross-seat delegated execution

`cross_seat ⇔ planning_outside_cursor ∨ execution_outside_cursor ∨ coordination_across_seats`. This does not change phase-doc shape; it adds density, harness, and bus-checkpoint rules.

### Spec-side density additions

For mechanical/cross-seat executors below Opus 4.8, phase doc must include before `## Tasks`:

```markdown
## Invariants (post-phase)
- properties preserved after phase
## Edge cases
| Case | Handling |
## Behavioral contracts
- Preconditions:
- Postconditions:
- Side effects allowed:
- Side effects forbidden:
```

If these cannot be specified, phase is under-designed; escalate or consult before dispatch.

### Harness selection

| Harness | Best for |
|---|---|
| `web-anthropic-direct` | default reasoning executor; sparse/architectural or when web is driving |
| `composer-2.5` / `cursor-sdk` | dense pseudocode/checklist via Cursor `/implement-plan` or `team_dispatch` |

`Expected Executor` may carry harness values (`web-anthropic-direct`, `composer-2.5`, `cursor-sdk`) or in-Cursor model tags. Long-term: denser plans route more to mechanical harnesses; sparse phases remain reasoning-executor work.

### Deployment pattern

For web-anthropic direct:

```text
phase_checkpointed ⇔ phase_count ≥3 ∨ ∃phase touches_dense_substrate ∨ projected_context_fill >0.6
```

Once checkpointed, one phase per session; hard stop at boundary. Composer/cursor-sdk use harness-native patterns.

### Phase-checkpointing protocol

Thread setup: create one agent_bus thread for the plan; sidecars at `notes/system/threads/{thread-id}-phase-{N}.md`.

At each phase boundary:
- post brief bus turn with status, files, API, deviations, next readiness, carry-over, outstanding debt, heads-up, discipline reminders, sidecar URI, next phase doc;
- sidecar includes negative-result findings, plan deviations with rationale, what prior session got wrong, reasoning notes;
- call `session_close` with `handoff_prompt` for Phase N+1 pickup. Do NOT `rj_write(kind="handoff")`.

Pickup session: boot, fetch full bus thread, read sidecar, read next phase doc, then pre-flight verify every heads-up against live code before drafting. Handoff is not authoritative on live state.

Execution within phase: code comments for every deviation (`# Deviation from plan §...`); quality gate on in-repo path, not scratch; run tests that can run.

Final boundary: `close=true` on bus thread after re-fetching latest turn to avoid stale close; write workspace wrap-up.

## Recurring Python style friction

Bake into Python-heavy phase docs and verification:

- Pre-collapse signatures/string concats that fit under 88 chars; run `ruff format` at author time, do not eyeball.
- Module-level imports; late imports only for cycles with `# noqa: I001`.
- Regex/format/explicit checks over `.replace()` placeholder substitution.
- Do not infer import package path from hyphenated dirs (`services/universal-stargate` ≠ `services.universal_stargate`).

Executor-side: verbatim block + `ruff format --check` are one contract. If block fails format, block is defective; run formatter, update block, do not escalate.

## Pipeline-construction phases

Phase doc must reference `.cursor/skills/build-pipeline/SKILL.md`; YAML/create files shown complete; handlers follow BEFORE/AFTER; verification adds validate pipeline, run pipeline/curl, observability trace; name namespace/step-type/timeout/sub-pipeline invariants.

## Anti-patterns

Authoring: prose-described code; elided BEFORE blocks; blank `Parallel-group`/`Depends-on`; parallel phases with overlapping files; mixed-density phase; >3 open questions; silent event-signal invention; missing arc wrap-up.

Coordinator: next group before all summaries; deriving handoff from plan docs instead of summaries; skipping Cortex seeding; abandoned `tmp/prompts/` without wrap-up.

## Minimal operating summary

Deck = README + numbered phase docs + summaries. Phase fields require explicit `None`. Modify tasks need complete BEFORE/AFTER; create tasks complete file. Verify `parallel_safe`. Inject invariant floor plus full architecture skill reads. Seed `plan:` + `plan_phase:` + `child_of` edge. Pipeline phases load build-pipeline. Bake Python friction checks. Write `summaries/00-{arc}-wrap-up.md`.
