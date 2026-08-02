Multitask by default for multi-phase plans.
Coordinator Mode: Dispatch → Await → Handoff (multi-phase)
Executor Mode: Read → Implement → Verify → Fix → Summary → Report (single phase)

**Load**: `@patterns_ws` `@async-verification_ws` `@quality-gates` `@core_ws` `@services_ws` `@topology_ws` `@event-debugging_ws` `@plan-slug-coherence_ws`

## Argument Forms

```
/implement-plan                              # scan ./tmp/prompts/ for phase-N.md files
/implement-plan tmp/prompts/my-plan/         # all phase-N.md in directory, sorted numerically
/implement-plan phase-1.md phase-3.md        # specific phases by filename (within plan dir)
/implement-plan tmp/prompts/my-plan/phase-1.md  # single phase → Executor Mode directly
```

**Mode detection**:
- Single phase file (explicit or resolved) → **Executor Mode** (existing sequence, steps 1–6)
- Directory OR multiple phase files → **Coordinator Mode** (Multitask, see below)

## Coordinator Mode (multi-phase, Multitask)

**Coordinator admission provenance:** the coordinator session stamps
`source_ref=plan:{name}` with `source_ref_derivation="ide-implement-plan-coordinator"`
at its `session_close` (container ref — not a leaf phase ref).

### Step C1: Resolve Phase Files
Parse argument → produce ordered list of phase docs.
- Directory arg: list all `phase-N.md` files, sort numerically by N.
- Filenames arg: resolve each relative to plan dir.
- No arg: scan `./tmp/prompts/` — if multiple plan dirs found, ask user which.

### Step C2: Read All Phase Docs
For each phase doc, extract:
- **Phase number** (from filename or `# Phase N:` heading)
- **Density** (from `**Expected Executor**` / `**Executor Mode**` fields)
- **Expected Files** (Create + Modify + Delete — union is the file footprint)
- **Depends-on** (explicit annotation, if present — see plan template)
- **Parallel-group** (explicit annotation, if present)

**Invariant pre-check**: For each phase doc, scan any code blocks for:
- `logging.getLogger` or `import logging` in application code → flag; plan must use `get_logger` from `universal_logging`
- New files projected >300 SLOC or existing files projected >400 SLOC → flag per `[sloc-gate]`
- Signal strings with underscores, hyphens, or digits → flag

Surface violations as warnings before dispatching any group. Do not block dispatch on warnings — surface and proceed.

### Step C3: Build Dispatch Groups
```
parallel_safe(A, B) ⟺
  ExpectedFiles(A) ∩ ExpectedFiles(B) = ∅
  ∧ ¬Depends-on(A, B)
  ∧ ¬Depends-on(B, A)
```
Group phases into ordered batches. Group 1 = phases with no Depends-on (or whose dependencies are already done). Explicit `Parallel-group` letter overrides inference: phases sharing the same letter dispatch together.

### Step C4: Tier Assignment per Density

| Phase density | Family | Effort | Thinking |
|---|---|---|---|
| Sparse / architectural (design decisions deferred) | Opus 5 | Low | on |
| Dense with pseudocode / concrete checklist | Sonnet 4.6 | Medium | on |
| Exploration / investigation | Sonnet 4.6 | High | on |
| Mechanical (rename, boilerplate, delete dead code) | Grok 4.20 / Sonnet | Low | off |

Instruct each subagent to run in **Executor Mode** at the assigned tier.

### Step C5: Dispatch Group N
Launch all phases in the current group as **parallel background subagents** (one per phase).
Each subagent runs Executor Mode (steps 1–6 below) for its single phase.
Include in each subagent prompt:
- Path to its phase doc
- Handoff payload from all prior completed phases (see C6)
- Instruction to produce the summary artifact at the derived path
- **Admission provenance:** `source_ref=plan_phase:{name}/phase-N` and
  `source_ref_derivation="ide-implement-plan-executor"` for the executor's
  `session_close` (provenance capture only — not rerouted through Stargate admission)
- Compact workspace invariants block (see below) — injected verbatim into system_context so it survives the subagent boundary

### Invariants block (inject into every executor system_context)

```
[logging]  ∀ application code: from universal_logging import get_logger — ¬ logging.getLogger() / ¬ import logging in app code
[sloc]     new files ≤ 300 SLOC; existing files ≤ 400 SLOC (SLOC = non-blank, non-comment, non-docstring lines)
[sloc-gate] pre-edit: SLOC >350 ∧ adding >20 ⟹ split first
[signals]  signal format: ^[a-z]+(\.[a-z]+){1,4}$ — no underscores, hyphens, digits in the signal string
[scope]    every changed line traces to the task — ¬ unrequested refactors or reformatting
[srp]      ∀ fn: (validation ∧ orchestration ∧ mutation ∧ I/O) ⟹ split
```

### Step C6: Await + Extract Handoff
After all group-N subagents complete:
- Read each phase summary from `./tmp/prompts/{name}/summaries/phase-N-summary.md`
- Compact handoff payload (passed to group N+1 subagents):
  ```
  Phase N complete:
  - Status: {Objective status from summary}
  - Files modified: {list from summary}
  - Deviations: {any deviations from plan, or "none"}
  - Next-phase readiness: {from summary}
  ```

### Step C7: Repeat
Dispatch next group with handoff payload seeded. Repeat until all phases done.

### Step C8: Final Report
- Confirm all `plan_phase:{name}/phase-{N}` Cortex entities exist (check via `cortex(tool="entity_get", ...)` for each)
- Emit a structured final report in the following form:

```
## Plan Complete: {name}

### Phase Summaries
| Phase | Objective | Status | Files Changed |
|-------|-----------|--------|---------------|
| N     | {one-line} | ✅ Done / ⚠ Partial / ❌ Failed | {count} files |

### Per-Phase File Changes
**Phase N** (`tmp/prompts/{name}/summaries/phase-N-summary.md`):
- Created: {list or "none"}
- Modified: {list or "none"}
- Deleted: {list or "none"}

(repeat for every phase)

### All Files Changed (union across all phases)
- Created: {sorted list or "none"}
- Modified: {sorted list or "none"}
- Deleted: {sorted list or "none"}

### Blockers / Deviations
{list, or "none"}
```

- All file paths must be derived from the phase summaries — ¬ infer from plan docs

## Workspace Extensions

**Pre-Implementation Checks**:

1. **Consultation Check** — if no `Consulted models:` marker in plan, suggest `/consult-plan` pre-plan variant (do not block)
2. **Phase Density** — classify before writing code (see Coordinator Mode tier table above for the full mapping).
   If current model is underqualified for the phase density: recommend user switch before proceeding.

**Post-Phase Review (RECOMMENDED)**: After summary artifact, suggest:
> "Phase {N} summary written. Review with `/review-phase @tmp/prompts/{name}/phase{N}.md`, fix gaps, `git add`, then proceed to phase {N+1}."

**Event-Driven Implementation (MANDATORY for behavior changes)**:
- New/changed behavior → add/update signals and payloads (see `patterns_ws.mdc`)
- Update `docs/architecture/` if plan specifies (use `docs/architecture/README_AI.md` index)

**Additional Quality Gates**:
- [ ] Event vocabulary covers new/changed behavior
- [ ] `docs/architecture/` synced (if plan had Documentation Impact)

## Executor Mode Sequence (single phase)

**Executor admission provenance:** stamp `source_ref=plan_phase:{name}/phase-N`
(`plan:{name}/phase-N` shorthand canonicalizes to the same form) with
`source_ref_derivation="ide-implement-plan-executor"` on `session_close`.

### 1. Read Plan
Locate plan | Study objectives/tasks/verification | Identify deliverables | Validate has checklist

### 2. Implement
Execute tasks sequentially | Follow specs exactly
- **SRP**: Apply splits (handlers ≤80 SLOC, <3 responsibilities)
- **Files**: Create only ∈ Expected Files
- **BC**: Question necessity, prefer clean breaks

### 3. Verify
- [ ] Compile: `python -m compileall -q {modules}/`
- [ ] Import: verify all imports resolve
- [ ] Lint: `ruff check {files}`
- [ ] Plan checklist: ∀ items pass
- [ ] Tests: ∀ commands succeed
- [ ] Invariants: `rg "logging\.getLogger\|^import logging" libs/ services/ --include="*.py"` returns no matches in files you modified

### 4. Fix
Fix immediately | Re-verify | Iterate until ∀ pass | ¬review files

### 5. Summary (MANDATORY)
You MUST create a phase summary file before the final response.

- **Path derivation**: if plan is `./tmp/prompts/{name}/phase-N.md` then summary path is `./tmp/prompts/{name}/summaries/phase-N-summary.md`
- **Required sections**: Objective status | Implemented work | File changes (create/modify/delete) | Verification commands and outcomes | Remaining risks/blockers | Next-phase readiness

### 6. Seed Cortex + Report

Before reporting, seed durable plan state in Cortex (summary path is ephemeral):

```
# 1. Ensure plan parent entity exists
cortex(tool="entity_create", arguments={
  "id": "plan:{name}",
  "type": "plan",
  "name": "{plan display name}",
  "workflow_state": "in_progress"   # update to "done" if this is the final phase
})
# entity_create is idempotent if the entity already exists — safe to call every phase

# 2. Create the phase entity
cortex(tool="entity_create", arguments={
  "id": "plan_phase:{name}/phase-{N}",
  "type": "plan_phase",
  "name": "Phase {N}: {objective}",
  "workflow_state": "done",
  "attributes": {
    "plan": "{name}",
    "phase": N,
    "session_id": "cursor-YYYY-MM-DD-HHmm",
    "files_modified": ["{list from summary}"]
  }
})

# 3. Link phase to parent plan (source=phase, target=plan, type=child_of)
cortex(tool="relationship_create", arguments={
  "source_id": "plan_phase:{name}/phase-{N}",
  "target_id": "plan:{name}",
  "type_id": "child_of",
  "session_id": "cursor-YYYY-MM-DD-HHmm",
  "agent": "cursor"
})
```

Then emit:
```
Phase {N} complete — {objective}
Summary: tmp/prompts/{name}/summaries/phase-N-summary.md
Files created:  {list or "none"}
Files modified: {list or "none"}
Files deleted:  {list or "none"}
Verification: {pass/fail per gate}
Next-phase readiness: {from summary}
```
Await next phase or coordinator handoff.

Do not mark implementation complete until summary file exists, has been read back
for verification, and the `plan_phase` entity is confirmed in Cortex.

## Quality Gates (NOT complete until)
- [ ] `python -m compileall` passes
- [ ] ∀ imports resolve
- [ ] `ruff check` passes
- [ ] Plan checklist ∀ pass
- [ ] Tests pass
- [ ] Summary created at derived path and read back for verification
- [ ] `plan_phase:{name}/phase-{N}` entity confirmed in Cortex
- [ ] ∀ files ∈ Expected Files
- [ ] ¬backward compat

## Error Handling
| Condition | Action |
|-----------|--------|
| Plan missing | Report, suggest create-implementation-plan |
| Verification fails | Fix, re-verify, iterate |
| Blockers | Document, pause |
| BC in plan | Question need, await confirmation |
