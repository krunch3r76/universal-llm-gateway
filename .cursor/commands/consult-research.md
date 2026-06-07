Two-step research consultation. Grounds implementation decisions in a research
corpus first, then validates the actual code via a source-code scope follow-up.

## When to Use

- Designing a new subsystem or feature where published research informs the approach
- Evaluating implementation choices against research findings
- Any task where the answer depends on both "what does the research say" and
  "does the code actually do that"

## Instructions

### 1. Identify Scopes and Files

Determine:
- **Research scope**: the RAG scope containing relevant papers/docs (e.g.
  `code_retrieval`, `llm_foundations`, `research`, `workflows`, `prompting`).
  Check `GET /scopes` or `~/.rag/config.yaml` if unsure what scopes exist.
- **Source files**: attach implementation files directly via `-f`. No source-code
  RAG scope exists yet — use file context for code grounding.
- **Context files**: the implementation files relevant to the question (≤6).

### 2. Research Scope Consultation

```bash
source ~/.venvs/universal/bin/activate
scripts/consult --scope {RESEARCH_SCOPE} -r researcher \
  -f {FILE_1} -f {FILE_2} ... \
  -o ./tmp/consult-research.md \
  "{QUESTION}"
```

Replace `{RESEARCH_SCOPE}` with the scope from step 1, use one `-f` per file
(`{FILE_1}`, `{FILE_2}`, ...), and set `{QUESTION}` to the design/evaluation question.

### 3. Source-Code Follow-Up

Reviewer role uses file context (no RAG scope needed for code review):

```bash
source ~/.venvs/universal/bin/activate
scripts/consult -r reviewer \
  --no-rag \
  -f {FILE_1} -f {FILE_2} ... \
  -o ./tmp/consult-research-source.md \
  "{FOLLOW_UP_QUESTION}"
```

Use one `-f` per implementation file, and set `{FOLLOW_UP_QUESTION}` to a
code-grounded question (e.g. "Review this implementation for correctness,
edge cases, and alignment with the research recommendations").

### 4. Synthesize

Read both output files. Produce a unified synthesis:
1. **Research consensus** — what the papers/docs recommend
2. **Implementation deltas** — where the code diverges or is incomplete
3. **Ordered next edits** — smallest safe sequence of changes
4. **Docstring-quality deltas (Python)** — any changed module/class/public function
   docstrings that are missing/thin vs overhaul baseline

For Python edits derived from this workflow, keep docstrings compatible with
overhaul/doc-generate expectations:
- module/class docstrings target ≥15 words
- public function docstrings target ≥10 words
- no placeholder/name-echo first sentence
- include purpose, invariants, and side effects when relevant

Present to the user before acting.

## Rules

- ¬ skip the research step — it prevents design decisions based solely on intuition
- ¬ skip the source-code step for implementation tasks — research-only validation
  misses code-level issues (see `consultation-workflow_ws.mdc` scope policy)
- ¬ hardcode scope names — always verify available scopes first
- ¬ loop more than twice — escalate to user if two rounds don't converge
- Output goes to `./tmp/consult-research*.md` (ephemeral, not committed)
- If Python files are touched, include docstring-quality improvements in the
  ordered edits using the same overhaul baseline
