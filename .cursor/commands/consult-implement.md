Mid-implementation consultation. Run when stuck or uncertain during active
coding. Sends the files you are currently working on to multiple models and
synthesizes concrete next steps.

## When to Use

- Stuck on the same problem after 2+ tool call attempts
- Uncertain about the right approach at an implementation branch point
- Hitting an unexpected error or invariant conflict with no clear path forward
- Need to understand how an unfamiliar module works before using it

## Instructions

### 1. Describe the Problem

Formulate a precise problem description:
- What you are trying to do (one sentence)
- What you have tried
- Where exactly you are stuck (file + line or function name)
- Any error messages or unexpected behaviour observed

### 2. Identify Context Files

Collect the files relevant to the problem — do NOT include unrelated open files:
- The file(s) you are currently editing
- Any file the error or problem directly references (imports, callers, dependencies)
- At most one architecture doc from `docs/architecture/` if the problem is architectural

Keep to ≤6 files. More context is not better — it dilutes the signal.

### 3. Run Consultation

If a research scope is relevant, add `--scope {scope}`:
- `workflows` — pipeline orchestration patterns
- `prompting` — prompt engineering research (all tiers)
- `code_retrieval` — code RAG techniques
- `research` — all research papers (broad)

Omit `--scope` for general implementation problems — the architect role
defaults to `workflows` scope.

```bash
source ~/.venvs/universal/bin/activate
python scripts/consult \
  -r architect \
  -f {FILE_1} -f {FILE_2} ... \
  -o ./tmp/consult-impl.md \
  "{PROBLEM DESCRIPTION}"
```

Use one `-f` flag per context file (`{FILE_1}`, `{FILE_2}`, ...), and set
`{PROBLEM DESCRIPTION}` from step 1.

### 4. Synthesize — Extract Next Steps

Read `./tmp/consult-impl.md`. Identify the consensus recommendation across
models (or note disagreement if present). Extract:

1. **Immediate next step** — the single most actionable thing to do right now,
   with the literal code change in a fenced code block (current → fixed).
   Read the source file if needed to produce the exact fix.
2. **Why** — the reasoning in one sentence
3. **Alternatives** — if models disagreed, note the other option briefly

If the immediate fix touches Python modules/classes/public functions, include
docstring quality as part of the recommendation so it stays compatible with
overhaul/doc-generate expectations:
- module/class docstrings target ≥15 words
- public function docstrings target ≥10 words
- avoid placeholder/name-echo first sentences
- include purpose, invariants, and side effects when relevant

Do NOT produce a full plan. This command is for getting unstuck, not for
full planning (use `/consult-plan` for that).

### 5. Act

Apply the recommended next step. If it resolves the problem, continue
implementation. If not, you may run `/consult-implement` once more with an
updated problem description that includes what you tried.

## Rules

- ¬ use this for architectural decisions from scratch — use `/consult-plan`
- ¬ include more than 6 files — keep context tight
- ¬ loop more than twice — if two consultation calls don't resolve the problem,
  escalate to the user rather than burning more tokens
- Output goes to `./tmp/consult-impl.md` (ephemeral, not committed)
- If Python files are touched, preserve or improve docstring quality to
  overhaul baseline (substantive, non-placeholder, audience-aware)
