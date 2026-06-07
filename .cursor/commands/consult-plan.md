if Pre-implementation consultation. Identifies relevant files via a scoping call,
then runs a deep planner consultation with those files as context. Synthesizes
the results into a structured implementation plan.

## Modes

| Invocation | Mode | What happens |
|---|---|---|
| `/consult-plan {task}` | **Plan** (default) | Full 9-step workflow: scoping → deep call → phase files |
| `/consult-plan --review {planname}` | **Review** | Gap analysis on existing phase files before implementation |

## When to Use

**Plan mode**: Before building anything that touches more than one file or involves
an architectural decision (new module, new data flow, new abstraction, new API).

Also use when a pre-plan already exists from in-thread discussion — the
consultation catches gaps the discussion didn't surface (event vocabulary,
invariant violations, failure modes). See step 6 variant for pre-plan framing.

**Review mode**: Phase files already exist at `./tmp/prompts/{planname}/phase*.md`
and you want frontier models to review them for gaps before running
`/implement-plan`. Use this between planning and implementation, or after
updating phase files with new context.

## Instructions — Review Mode

If the user invokes `/consult-plan --review {planname}`, skip to this section.

### R1. Collect Phase Files and Source Files

Glob `./tmp/prompts/{planname}/phase*.md`. Read each phase file. From the
"Expected Files" sections, collect all source files that will be modified.

Verify each source file exists. Include `docs/event-contracts.md` if any phase
has event vocabulary changes.

### R2. Run Gap Analysis via `consult-frontier`

```bash
source ~/.venvs/universal/bin/activate
# REMINDER: use a heredoc prompt variable for long consult prompts.
# Never inline a large quoted prompt directly on the command line:
# shell interpolation/quoting can break and execute unintended tokens.
PROMPT="$(cat <<'EOF'
The attached phase documents describe the implementation approach (settled).

Review for completeness, gaps, AND architectural soundness:
1. Root cause vs. symptom: is this plan treating a root cause or adding a
   compensating layer (reconciliation, watchdog, retry wrapper)? If a simpler
   design would dissolve the problem, describe the structural alternative
   even if the plan is technically correct.
2. Event vocabulary: what signals should be added/modified/deprecated?
   Map each behavioral change to a signal.
3. Invariant preservation: are existing contracts maintained?
4. Failure modes: are error paths observable and handled?
5. Coordination boundaries: are concurrent interactions signaled?
6. SRP: any function >1 responsibility or handler >80 SLOC?
7. Async: any blocking I/O in async context?
8. Error handling: capacity errors using canonical envelope?
9. Documentation: does docs/architecture/ need updating?
10. Docstring quality: for changed Python modules/classes/public functions,
    are docstrings substantive enough for overhaul/doc-generate extraction
    (module/class target ≥15 words, public function target ≥10 words,
    no name-echo placeholders)?
11. Simplification opportunities: what existing code could be removed or
    restructured to make this change smaller or unnecessary?
12. Concurrency invariant: does any proposed change introduce asyncio.Lock
    or asyncio.Semaphore? This project FORBIDS both. Concurrency hierarchy:
    atomic ops > events > routing > sequential > queues > locks.
    Bounded concurrency uses asyncio.Queue with N workers, not semaphores.

For each gap found, provide the concrete fix (code, config, or signal
definition) — not just a description of the problem.
EOF
)"

./scripts/consult-frontier \
  --no-rag \
  --chain \
  -f {PHASE_1} -f {PHASE_2} ... \
  -f {SOURCE_FILE_1} -f {SOURCE_FILE_2} ... \
  -o ./tmp/consult-plan-review.md \
  "$PROMPT"
```

### R3. Synthesize and Apply

Read `./tmp/consult-plan-review.md`. For each gap found:
1. Validate against workspace rules and project invariants (consultation
   models lack rule awareness)
2. Apply valid fixes to the phase files
3. Present the synthesis to the user: what was found, what was updated,
   what was rejected and why

### R4. Confirm

Show the user the updated phase file locations and changes. Ask:
"Phase files updated with review feedback. Proceed with `/implement-plan`?"

## Instructions — Plan Mode

### 1. Understand the Task

Read the user's task description from the conversation. If it is ambiguous,
ask one clarifying question before proceeding.

### 2. Scoping Call — Identify Relevant Files

Architecture docs are not injected — they are outdated and not maintained automatically.

Run (`--cloud-only` ensures architect role uses cloud models; local models hallucinate file paths):
```bash
source ~/.venvs/universal/bin/activate
python scripts/consult \
  -r architect \
  --no-rag \
  --cloud-only \
  -o ./tmp/consult-scope.md \
  "Task: {TASK}

List the specific source files, config files, and docs/architecture/ files
that are directly relevant to this task. For each file, give one sentence
explaining why it is relevant. Format each file path on its own line
starting with 'FILE: ' so it can be parsed."
```

Replace `{TASK}` with the user's task description.

### 4. Parse Relevant Files

Read `./tmp/consult-scope.md`. Extract every line starting with `FILE: `.
Strip the prefix to get a list of file paths.

Verify each path exists (use the file reading tool). Discard any that do not
exist. Keep the list to ≤10 most relevant files — if more than 10 are
identified, prioritise by how central they are to the task.

### 4b. Report Hallucinating Scoping Models (MANDATORY)

If the scoping output contains invalid `FILE:` paths, report the responsible
scoping model(s) before proceeding to the deep planner call.

1. Read `./tmp/consult-scope.md` and capture:
   - `**Selected models**: ...` (comma-separated list)
   - invalid `FILE:` paths (those that failed existence checks)
2. Compute a simple hallucination ratio:
   - `invalid_count / total_file_lines`
3. Reporting gate:
   - Report when `invalid_count >= 2` **and** ratio `>= 0.30`
4. For each selected model, call:

```bash
curl -sS -X POST "http://localhost:9999/api/v1/report-model" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $STARGATE_API_KEY" \
  -d '{
    "task": "code_architecture",
    "model_id": "MODEL_ID",
    "reason": "path_hallucination",
    "details": {
      "role": "architect",
      "invalid_paths": ["..."],
      "invalid_count": 0,
      "total_file_lines": 0,
      "hallucination_ratio": 0.0,
      "source": "consult-plan.scoping"
    }
  }'
```

Notes:
- If auth is disabled locally, the `Authorization` header may be omitted.
- Do not block planning on report failures; log the failure and continue with
  validated files.
- This reporting step is additive to local exclusion behavior (`scripts/consult`
  grounding guard); both should run.

### 5. Read the Files and Assess Existing Complexity

Read each identified file. This is your injected context for the deep call.
If a file is very large (>300 lines), read only the first 80 lines to get
the structure, plus any section directly relevant to the task.

**Simplification prep** (do this while reading): as you read, note any
patterns that suggest accumulated complexity or prior workarounds:
- Reconciliation loops (re-syncing state that drifted between components)
- Watchdogs / TTL timers (compensating for unreliable state transitions)
- Retry/timeout wrappers around operations that should succeed reliably
- Defensive checks that exist because an invariant isn't structurally enforced
- Multiple components tracking the same truth independently
- Files >400 SLOC or functions >60 SLOC in the task's path

Capture these as a brief "Existing Complexity" note (3-5 bullet points max).
You will inject this into the deep call prompt so frontier models can evaluate
whether the task addresses root cause or adds another compensating layer.

### 6. Deep Call — Planner Consultation

If a research scope is relevant to the task domain, add `--scope {scope}` and omit `--no-rag` so the planner draws from the RAG corpus. Corpora are under `docs/research/` (see `consultation-workflow_ws.mdc` scope table). Architecture docs are not injected regardless.

**Recommended scopes by task:**
- RAG / retrieval / chunking / reranking → `rag_systems` or `research`
- Pipeline orchestration, multi-step, consensus → `workflows`
- Prompt engineering → `prompting` or `small_llm_prompting` / `llm_prompting`
- Code retrieval, AST chunking → `code_retrieval`
- PKM, agent memory, knowledge graphs → `knowledge_management` or `knowledge_systems`
- LLM internals / reference → `llm_foundations`
- Broad or cross-domain → `research` or `all_research`

Omit `--scope` for pure implementation tasks with no research angle — the planner then uses only `-f` file context. Architecture docs are not injected (outdated, not maintained automatically).

Use `scripts/consult-frontier` for the deep call — pass `--no-rag` so RAG (including architecture docs) is skipped:
```bash
source ~/.venvs/universal/bin/activate
# REMINDER: build prompt via heredoc variable; do not inline long quoted prompts.
PROMPT="$(cat <<'EOF'
{TASK}

## Existing Complexity (observed by the agent)
{COMPLEXITY_NOTES — the bullet points from step 5, or 'None identified' if clean}

Evaluate whether the proposed change addresses a root cause or compensates
for accidental complexity. If a simpler architecture would avoid the problem
entirely, present both options: the targeted fix AND the structural
simplification with trade-offs for each.

## Project Concurrency Invariant (CRITICAL — must not violate)
This project FORBIDS asyncio.Lock and asyncio.Semaphore. The concurrency
preference hierarchy is: atomic ops > events > routing > sequential > queues > locks.
Bounded concurrency uses asyncio.Queue with N worker tasks (see watcher_manager.py,
rag_service.py reconciliation). Do NOT propose semaphores or locks; if concurrency
bounding is needed, use queue-based worker pools or reduce the worker count.

Also analyze event vocabulary impact: what event signals should be added,
modified, or deprecated to maintain observability of this change? Reference
docs/event-contracts.md for existing signal vocabulary. For each behavioral
change (new flow, state transition, decision point, failure mode, concurrent
boundary), map it to a signal.

If the proposed change touches Python modules/classes/public functions,
include docstring quality requirements in the plan so the result is compatible
with overhaul/doc-generate quality expectations:
- module/class docstrings target ≥15 words
- public function docstrings target ≥10 words
- avoid placeholder/name-echo first sentences
- include purpose, caller context, invariants, and side effects when relevant.
EOF
)"

./scripts/consult-frontier \
  --no-rag \
  --chain \
  -f {FILE_1} -f {FILE_2} ... \
  -o ./tmp/consult-plan.md \
  "$PROMPT"
```

Use one `-f` flag per file from step 4 (`{FILE_1}`, `{FILE_2}`, ...), and set
`{TASK}` to the user's task description. Replace `{COMPLEXITY_NOTES}` with the
bullet points from step 5's simplification prep — this primes the frontier
models to evaluate structural alternatives rather than only the proposed approach.

Always include `docs/event-contracts.md` as one of the `-f` files so planner
models can reference the existing signal vocabulary.

`consult-frontier` hardcodes the frontier model panel (GPT-5.4 Pro, Claude Opus
4.6, Gemini 3.1 Pro, Grok 4) and a 360s timeout. It forwards all other args to
`consult -r planner`. For non-architectural consultations or when specific models
are needed, call `scripts/consult` directly instead.

**Pre-plan variant**: If a complete plan document exists from in-thread discussion
(e.g., a `phase{n}.md`), include it as a `-f` file and frame the prompt as gap
analysis rather than plan generation:

```
"The attached plan document describes the implementation approach (settled).

## Existing Complexity (observed by the agent)
{COMPLEXITY_NOTES — bullet points from step 5, or 'None identified'}

Review for completeness, gaps, AND architectural soundness:
1. Root cause vs. symptom: is this plan treating a root cause or adding a
   compensating layer? If a simpler design would dissolve the problem, say so.
2. Event vocabulary: what signals should be added/modified/deprecated?
   Map each behavioral change to a signal.
3. Invariant preservation: are existing contracts maintained?
4. Failure modes: are error paths observable and handled?
5. Coordination boundaries: are concurrent interactions signaled?
6. SRP: any function >1 responsibility or handler >80 SLOC?
7. Async: any blocking I/O in async context?
8. Error handling: capacity errors using canonical envelope?
9. Documentation: does docs/architecture/ need updating?
10. Docstring quality: for changed Python modules/classes/public functions,
    are docstrings substantive enough for overhaul/doc-generate extraction
    (module/class target ≥15 words, public function target ≥10 words,
    no name-echo placeholders)?
11. Simplification opportunities: what existing code could be removed or
    restructured to make this change smaller or unnecessary?
12. Concurrency invariant: does any proposed change introduce asyncio.Lock
    or asyncio.Semaphore? This project FORBIDS both. Concurrency hierarchy:
    atomic ops > events > routing > sequential > queues > locks.
    Bounded concurrency uses asyncio.Queue with N workers, not semaphores.

For each gap found, provide the concrete fix (code, config, or signal
definition) — not just a description of the problem."
```

This subsumes `/review-prompt` with multi-model input. The gap analysis prompt
covers the same checklist concerns but gets fresh eyes from planner models
rather than relying on the in-thread agent alone.

### 7. Synthesize — Produce Implementation Plan

Read `./tmp/consult-plan.md`. Synthesize the multi-model responses into a
single structured implementation plan.

**Plan name**: derive a short kebab-case name from the task (e.g.
`token-budgeting`, `rag-unix-socket`, `cancel-group`). This becomes the
directory name under `tmp/prompts/`.

**CRITICAL**: Each task MUST include the literal code, YAML, config, or content
to write — in fenced code blocks — so the executor model can copy-paste rather
than interpret natural language descriptions. If the consultation responses
include code snippets, use them directly. If they don't, write the code yourself
based on the consultation's guidance and the source files you read in step 5.

First, write the unified synthesis to `./tmp/consult-plan-final.md` using this
template:

```markdown
# Plan: {Task}

**Consulted models**: {list from consult-plan.md}
**Relevant files**: {list from scoping}

## Objective
{1-2 sentences}

## Simplification Assessment
{From the consultation: is this addressing root cause or symptom? If frontier
models identified a structural alternative, describe it here with trade-offs.
If all models agree the complexity is essential, state that.}

## Approach
{Chosen approach with brief rationale. Note any alternatives the consultation
identified and why this one was selected. If a simplification path was
identified, note whether it is being pursued now or deferred (and why).}

## Phase Summary
| Phase | Title | Tasks | Key deliverable |
|---|---|---|---|
| 1 | {title} | {task numbers} | {one-line} |
| 2 | {title} | {task numbers} | {one-line} |

## Risks / Notes
{Any caveats, invariants to preserve, or gotchas from the consultation}
```

### 8. Decompose into Phase Files (PRIMARY OUTPUT)

**This step produces the deliverable.** The phase files are what `/implement-plan`
consumes. ¬ skip this step. ¬ treat the consultation output as the final artifact.

Create directory `./tmp/prompts/{planname}/` and write one file per phase.

For single-phase plans ("Single phase — atomic"), still write `phase1.md` — the
structure is uniform regardless of phase count.

**COMPLETENESS INVARIANT (CRITICAL)**

∀ task in phase file: a lower-tier (fast/cheap) model must be able to execute
it with ZERO additional reasoning or source-file reading. This means:

- **For `modify` tasks**: show the complete current function/block being changed
  AND the complete replacement. Use before/after format or a full replacement
  block with enough surrounding context (3-5 lines before + after) to locate
  the exact insertion point unambiguously.
- **For `create` tasks**: show the complete file content — every import, every
  function, every docstring. Nothing omitted.
- **For YAML/config tasks**: show the full stanza being changed with the
  surrounding keys needed to locate it.
- ¬ show partial code with comments like `# ... rest stays the same`,
  `# existing code`, or `# insert after X` — these require the executor to
  read and interpret source, which defeats the purpose.
- ¬ describe the code in prose and expect the executor to write it. Write the
  code yourself, now, during step 8.

**Self-check before writing each task block**: "Could a model that has NOT read
the source file apply this change correctly?" If no → expand the code block.

Each phase file uses the `/create-implementation-plan` template:

```markdown
# Phase N: {Title}

**Expected Executor**: {model}
**Executor Mode**: {thinking | non-thinking}
**Optional Consultation**: {model: question (success: criteria)} or None

## Objective
{1-2 sentences, scoped to this phase only}

## Tasks
### Task 1: {Name}
**File**: `{path}`
**Action**: create | modify | delete
**Pattern**: {Strategy|Factory|Observer|...} or None (custom)

{One sentence: what changes and why.}

```python
# BEFORE (lines M-N of file — enough context to locate exactly)
{complete current code of the function/block/stanza being replaced}
```

```python
# AFTER (complete replacement — copy-paste ready)
{complete new code}
```

For new files, a single block with the complete file content replaces the
before/after pair.

### Task 2: ...

## Event Vocabulary
| Behavioral change | Signal | Action |
|---|---|---|
| {new flow / state transition / decision point} | `{signal.name}` | add / modify / none needed |

∀ new signals: include `@event_factory` function + payload definition in the
relevant Task above. ∀ modified signals: show payload changes.
If no event changes needed, state: "No event vocabulary changes — {reason}."

## Verification
- [ ] Compile: `python -m compileall -q {modules}/`
- [ ] Lint: `ruff check {files}`
- [ ] Events: `docs/event-contracts.md` updated for new/changed signals
- [ ] {any domain-specific checks from the consultation}

## Expected Files
Create: {paths} | Modify: {paths} | Delete: {paths}
```

Phase 2+ files should note dependencies on prior phases (e.g. "Depends on
Phase 1 summary for X"). `/implement-plan` writes summaries to
`./tmp/prompts/{planname}/summaries/phase-N-summary.md` after each phase.

### 9. Present

Show the plan to the user (unified synthesis + phase file locations). Ask:
"Proceed with implementation?" before writing any code. Do NOT begin
implementing until the user confirms.

## Rules

### Plan mode
- ¬ skip the scoping call — it prevents injecting irrelevant files into the deep call
- ∀ scoping hallucination above threshold: report via `/api/v1/report-model` before deep call
- ¬ include more than 10 files in the deep call — keeps the context signal-to-noise high
- ¬ proceed to implementation without user confirmation
- ¬ invent file paths — only use paths confirmed to exist
- ¬ hardcode local model IDs (`--models <local-id>`) — local models hallucinate file paths on architect/planner roles; use `--cloud-only` and let role auto-selection pick
- ∀ plans: produce phase files at `./tmp/prompts/{planname}/phase*.md` — this is what `/implement-plan` consumes
- Output files go to `./tmp/` (ephemeral, not committed)
- ∀ task code blocks: complete, copy-paste ready — ¬ partial snippets, ¬ prose-described code, ¬ `# ... rest unchanged` placeholders (see step 8 COMPLETENESS INVARIANT)
- If Python files are in scope, phase tasks must preserve/improve docstring quality
  for overhaul/doc generation (module/class target ≥15 words; public function
  target ≥10 words; no placeholder/name-echo docstrings)

### Review mode
- ¬ run review mode without existing phase files — if `./tmp/prompts/{planname}/phase*.md` is empty, fall back to plan mode
- ∀ review suggestions: validate against workspace rules before applying to phase files
- ¬ apply review suggestions that violate project invariants — reject with explanation
- ¬ proceed to implementation without user confirmation after review
