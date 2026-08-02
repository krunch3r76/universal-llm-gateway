Post-phase spec-fidelity review. Runs a single **consultation pass** (see
**Model semantics** below) on the phase summary + git diff to catch executor
gaps before the next phase builds on this one.

## When to Use

After completing a phase via `/implement-plan`, before starting the next phase.
This is a per-phase checkpoint — run it after each phase's summary is written.

```
implement phase N → summary → /review-phase @path/to/phaseN.md
  → fix gaps → git add (stage reviewed source files) → next phase
```

Staging reviewed source files after review ensures `/consult-review`
(pre-commit, code quality) does not re-examine already-reviewed code.
Do not include phase docs or review artifacts in this staging recommendation.

## Invocation

`/review-phase {phase_file}`

The argument is the phase spec file path. The command derives all other paths
from it:

- **Phase spec**: the provided file (e.g., `tmp/prompts/my-plan/phase1.md`)
- **Summary**: `{parent}/summaries/{stem}-summary.md` (e.g., `tmp/prompts/my-plan/summaries/phase1-summary.md`)
- **Review output**: `{parent}/reviews/{stem}-review.md` (e.g., `tmp/prompts/my-plan/reviews/phase1-review.md`)

Example: `/review-phase @tmp/prompts/rag-metadata-consolidation/phase1.md`

## Instructions

### 1. Derive Paths

Given the phase file path, extract:
- `PARENT` — the directory containing the phase file
- `STEM` — the filename without extension (e.g., `phase1`)
- `SUMMARY` — `{PARENT}/summaries/{STEM}-summary.md`
- `REVIEW_OUT` — `{PARENT}/reviews/{STEM}-review.md`

Read both the phase spec and the summary. If the summary does not exist, stop
— the phase implementation is incomplete.

### 2. Collect Changed Files

From the summary's "File changes" section, extract the list of modified/created
files. Compute the diff:

```bash
git diff -- {FILE_1} {FILE_2} ... > /tmp/review-phase-diff.patch
git diff --stat -- {FILE_1} {FILE_2} ...
```

If the diff is empty (files already staged), use `git diff --cached` instead.

### 2.5 Event Signal Literal Gate (MANDATORY)

Before model review, run a static event-signal literal check on the changed files.
This catches invalid underscore/hyphen signal names before runtime emission.

```bash
rg -n 'signal\s*=\s*"[^"]*[_-][^"]*"' {FILE_1} {FILE_2} ...
```

If any match is found:
- Stop and fix signal names to dot segments (`foo.bar.baz`).
- Ensure each segment matches `[a-z]+` and total segments are 2-5.
- Re-run this gate before continuing.

### 3. Model semantics (`-r reviewer`)

The sample invocation uses `-r reviewer` and `--models openrouter/openai/gpt-5.4`. That does
**not** guarantee the literal string `openai/gpt-5.4` is the only model that runs:

- **Reviewer = pipeline mode**: `scripts/consult` forces `--pipeline` for
  `reviewer`. Execution goes through the **`code-review`** virtual model
  (`pipelines/code_review/chain.yaml`: review → validate → merge), not a single
  raw chat completion.
- **`--models` → `model_ref_overrides`**: The first ID overrides pipeline ref
  **`review_model`**; the second (if present) overrides **`validate_model`**.
  With only one ID, **`validate_model`** still comes from
  `pipelines/code_review/models.yaml` (currently `openrouter/openai/gpt-5.3-codex`).
- **No `--models`**: Stargate **selects** models for the reviewer role (sticky
  key `consult-review` in `scripts/consult-roles.yaml`). Selection can return
  **local** catalog models (for example Hermes-class IDs) when that matches
  requirements and availability — not necessarily a frontier cloud ID.
- **Fallback**: If the primary cloud model raises `ProxyClientError`, the
  generate handler may try **fallback** candidates from the same step’s
  `model_requirements`, which can change the effective model mid-run.

To see what actually ran: consult stderr (`Pipeline reviewer model overrides`,
batch messages), consult run artifacts, or Event Service
`pipeline-trace` / `ModelFallbackResolved` for the execution.

### 4. Run consultation review

```bash
source ~/.venvs/universal/bin/activate

PROMPT="$(cat <<'EOF'
The attached files are:
1. A phase specification (the intended implementation plan)
2. A phase summary (what was actually implemented)
3. A git diff of the changed files

Review the implementation for:

1. **Spec fidelity**: Does the diff implement everything the phase spec
   describes? List any tasks from the spec that are missing or incomplete
   in the diff.

2. **Correctness gaps**: Are there off-by-one errors, missing error handling,
   wrong variable names, type mismatches, or logic bugs introduced by the
   executor?

3. **Missed cleanup**: Does the spec call for removing code, imports, or
   config fields that are still present in the diff? Are there stale
   references the executor forgot to update?

4. **Event vocabulary**: If the spec describes event signal changes, are they
   correctly implemented in the diff? Are payloads complete?

4.1 **Signal format correctness**: Validate every changed signal literal against
   `^[a-z]+(\.[a-z]+){1,4}$` (no underscores, digits, or hyphens in segments).
   Treat invalid signal format as a Critical finding.

5. **Docstring quality**: Do changed Python modules/classes/public functions
   have substantive docstrings (module/class ≥15 words, function ≥10 words)?
   Flag placeholder or name-echo text.

For each finding, provide the concrete fix (file path, current code, corrected
code). Do not describe problems without solutions.
EOF
)"

./scripts/consult \
  -r reviewer \
  --models openrouter/google/gemini-3-flash-preview openrouter/google/gemini-3-flash-preview  \
  --no-rag \
  -f {PHASE_SPEC} \
  -f {SUMMARY} \
  -f /tmp/review-phase-diff.patch \
  -o {REVIEW_OUT} \
  "$PROMPT"
```

### 5. Present Findings

Read the review output file. For each finding:

1. Validate against workspace rules (consultation models lack rule awareness)
2. Apply fixes that pass validation
3. Report rejected findings with the conflicting rule

### 6. Amend Summary

If any fixes were applied, append a "## Post-review amendments" section to the
summary file. This keeps the executor's original snapshot intact while ensuring
the next phase's executor sees the true final state.

```markdown
## Post-review amendments

Review: `reviews/{STEM}-review.md`

- Fixed: {one-line description of each applied fix}
- Rejected: {count} findings ({brief reason or "see review file"})
```

If no fixes were needed, skip this step.

### 7. Recommend Staging

After all valid fixes are applied:

```
Review complete. Stage reviewed source files before next phase:
  git add {FILE_1} {FILE_2} ...
```

List only the specific implementation/source files from the diff.
Do not recommend staging `phase*.md`, `summaries/*`, `reviews/*`, or prompt
artifacts created by this command.

## Rules

- ¬ run without a phase summary — implementation must be complete first
- ¬ use RAG — this is a focused diff review, not architecture consultation
- ¬ skip invariant validation — the consultation model lacks project rule awareness
- ¬ apply fixes without presenting them — findings are recommendations, not auto-applied
- Single model, single pass — target cost ~$0.20-0.40 per phase
- Always recommend staging reviewed source files after review to separate from
  `/consult-review` scope
