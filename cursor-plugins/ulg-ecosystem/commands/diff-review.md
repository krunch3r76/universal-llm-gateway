Review either the current meaningful `git status` file set, or an explicitly
requested file/range, against workspace invariants and shared rules.

Related skills:
- `agent-skills/architecture-invariants.md` (cortex) — universal invariant layer; MCP reviewers MUST read via `fs` before findings
- `agent-skills/ulg-architecture.md` (cortex) — ULG repo layer; pair with architecture-invariants for this workspace
- `.cursor/skills/review-task-guidance/SKILL.md` — shared task_guidance packet content
- `.cursor/skills/multi-model-review/SKILL.md` — adversarial multi-reviewer chain pattern

Load `architecture-handoff-protocol.mdc` § "Block 2" and § "Block 5" when building any handoff packet. Copy skeleton from `tmp/reviews/_handoff-packet-template.md`; transport axis in `agent-skills/consult-routing.md`.

Related commands:
- `/review-apply` — cross-session apply of findings from any diff or session review artifact or agent-bus thread (replaces `/diff-review-apply`)

Unlike `/consult-review` (unstaged file quality, pre-commit) and `/review-phase`
(spec fidelity, per implementation phase), this command reviews either:
- the **current modified/added working-tree file set**, or
- an **explicitly requested file/path and optional `since` ref**

for architectural conformance, invariant violations, and cross-cutting quality
issues.

Default scope is intentionally narrow: only files currently reported by
`git status` as modified, added, or untracked are eligible. Pure deletions are
excluded.

Override scope is also supported: if the user supplies a file/path and/or
`since <git-ref>`, review that explicit selection even if the change has
already been committed and no longer appears in `git status`.

Default mode uses **`web-claude`**: `team_dispatch(op="handoff")` → `claude-web`.
Claude Web uses its full MCP toolset and supports multi-turn dialectic until
convergence. Packet on disk; Stargate posts a short bus pointer.

**`claude-cursor`**: same handoff primitive → dedicated Cursor IDE thread.

Other dispatch modes:
- `team-reviewer` — select via any resolved model token (`gpt-5.5`, `gemini`,
  `provider/model`, etc.) or explicit `team-reviewer`. Synchronous
  `team_dispatch(op=generate, role=reviewer, …)` with MCP tools (`fs`, `cortex`,
  `observability`, `rag`). Optional `model=` override within role `allowed_models`.
  Runs manifest expansion before dispatch; claude-web is the cognitive fallback
  when Critical/Warning findings reference files outside the manifest. Requires
  Stargate to be running.
- `team-inline` / `raw` — `team_dispatch(op=generate, role=synthesizer)` —
  inline-only (no MCP writes); corpus inlined in the prompt. Use when MCP catalog
  is unavailable or you want a single-shot read of a small diff only.
- `--grok` — grok-build as primary dispatcher with manifest expansion.
  claude-web is the cognitive fallback on stdout truncation (non–Suggestion-only)
  or when `--ab` is set.
- `agent:orion` / `agent:bard` — reserved stubs. Persona boot deferred until
  AGENTS.md is live and empirical evidence confirms persona context adds value
  beyond a generic MCP reviewer prompt + Cortex tool access.

## When to Use

- Before committing or merging a focused set of current working-tree changes
- After committing, when you want to review a specific file or range such as
  "since the penultimate commit"
- When you want an architectural perspective that `/consult-review` doesn't
  cover (invariant violations, contract mismatches, cross-module design issues)
- Complementary to `/consult-review`, not a replacement: run both on large branches

## Invocation

```
/diff-review [model] [path] [since <git-ref-or-alias>]
```

`model` — optional model family, `gpt-5.x` shorthand, or full model ID.
Default (omitted): `web-claude`.

`path` — optional file or directory path to review. If present, it overrides
the default `git status` file discovery and narrows the review to that path.

`since` — optional explicit review baseline. If present, review changes since
that ref instead of using `git status`-only discovery. Helpful after a commit.

Accepted aliases:
- `since penultimate commit`
- `since the penultimate commit`
- `since second-to-last commit`

All resolve to `HEAD~1`.

| Argument | Mode | Model | claude-web |
|---|---|---|---|
| omitted | `web-claude` | `claude-web` | primary |
| `openai` | `team-reviewer` + expansion | `openai/gpt-5.5` | fallback (gap-triggered) |
| `gpt-5.5` / `gpt-5` | `team-reviewer` + expansion | `openai/gpt-5.5` | fallback (gap-triggered) |
| `gpt-5.4` | `team-reviewer` + expansion | `openai/gpt-5.4` | fallback (gap-triggered) |
| `gemini` | `team-reviewer` + expansion | `google/gemini-3-pro-preview` | fallback (gap-triggered) |
| `team-reviewer` | `team-reviewer` + expansion | `openai/gpt-5.5` | fallback (gap-triggered) |
| `team-reviewer gemini` | `team-reviewer` + expansion | `google/gemini-3-pro-preview` | fallback (gap-triggered) |
| `team-reviewer <full-model-id>` | `team-reviewer` + expansion | as supplied | fallback (gap-triggered) |
| `openai/gpt-5.5` (or any `provider/model`) | `team-reviewer` + expansion | as supplied | fallback (gap-triggered) |
| `team-inline` / `raw` | raw frontier (no MCP) | `openai/gpt-5.5` unless overridden | — |
| `--grok` | grok-build + expansion | grok-build | fallback (gap-triggered) |
| `--grok --ab` | grok-build + expansion | grok-build | always (A/B) |
| `web-claude` | `web-claude` (explicit) | claude-web | primary |
| `claude-web` | `web-claude` | claude-web | primary |
| `claude-cursor` / `cursor-claude` | `cursor-claude` | claude-cursor | primary |
| `agent:orion` | reserved stub — see "Other dispatch modes" | — | — |
| `agent:bard` | reserved stub — see "Other dispatch modes" | — | — |

Examples:

```
/diff-review                            # web-claude (default)
/diff-review web-claude                 # web-claude (explicit)
/diff-review claude-web                 # team_dispatch handoff → claude-web
/diff-review claude-cursor              # team_dispatch handoff → claude-cursor
/diff-review gpt-5.5                    # team-reviewer + openai/gpt-5.5
/diff-review openai/gpt-5.4             # team-reviewer + openai/gpt-5.4
/diff-review gemini
/diff-review team-reviewer
/diff-review team-reviewer gemini
/diff-review team-inline               # no MCP — inlined corpus only
/diff-review --grok                     # grok-build primary, claude-web fallback (gap-triggered)
/diff-review --grok --ab                # explicit A/B mode
/diff-review libs/transport_utils/client_factory.py
/diff-review gpt-5.5 libs/transport_utils/client_factory.py
/diff-review --grok libs/transport_utils/client_factory.py
/diff-review libs/transport_utils/client_factory.py since HEAD~1
/diff-review libs/transport_utils/client_factory.py since the penultimate commit
/diff-review anthropic/claude-opus-4-7  # team-reviewer + claude-opus-4-7
```

## Instructions

### 0. Resolve Model, Path, and Since Ref

Parse the arguments supplied after `/diff-review`.

Important disambiguation rule:
- **Check for an existing repo path before treating a token containing `/` as a model ID.**
- This avoids misreading `libs/transport_utils/client_factory.py` as a model.

```
MODEL_FAMILIES = {
    "openai": "openai/gpt-5.5",
    "gemini": "google/gemini-3-pro-preview",
}

# Bare OpenAI GPT-5 version shorthands — always resolve to team-reviewer.
GPT_VERSION_ALIASES = {
    "gpt-5.5": "openai/gpt-5.5",
    "gpt-5.4": "openai/gpt-5.4",
    "gpt-5": "openai/gpt-5.5",
}

REVIEW_MODEL = "claude-web"
REVIEW_MODE = "web-claude"
BOOT = None
AB_MODE = False
PATH_ARG = None
SINCE_ARG = None

tokens = all args after /diff-review
i = 0
while i < len(tokens):
    tok = tokens[i]

    if tok == "since":
        SINCE_ARG = " ".join(tokens[i + 1:]).strip() or None
        break

    if repo_path_exists(tok):
        PATH_ARG = tok
        i += 1
        continue

    if tok in {"team-inline", "raw"}:
        REVIEW_MODE = "frontier"
        BOOT = "none"
        i += 1
        continue

    if tok in MODEL_FAMILIES:
        REVIEW_MODEL = MODEL_FAMILIES[tok]
        REVIEW_MODE = "team-reviewer"
        BOOT = "mcp"
        i += 1
        continue

    if tok in GPT_VERSION_ALIASES:
        REVIEW_MODEL = GPT_VERSION_ALIASES[tok]
        REVIEW_MODE = "team-reviewer"
        BOOT = "mcp"
        i += 1
        continue

    if tok in {"web-claude", "claude-web"}:
        REVIEW_MODE = "web-claude"
        REVIEW_MODEL = "claude-web"
        i += 1
        continue

    if tok in {"claude-cursor", "cursor-claude", "cursor-lead"}:
        REVIEW_MODE = "cursor-claude"
        REVIEW_MODEL = "claude-cursor"
        i += 1
        continue

    if tok == "--grok":
        REVIEW_MODE = "grok-build"
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        if next_tok == "--ab":
            AB_MODE = True
            i += 2
        else:
            i += 1
        continue

    if tok == "team-reviewer":
        REVIEW_MODE = "team-reviewer"
        BOOT = "mcp"
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        if next_tok in MODEL_FAMILIES:
            REVIEW_MODEL = MODEL_FAMILIES[next_tok]
            i += 2
        elif next_tok in GPT_VERSION_ALIASES:
            REVIEW_MODEL = GPT_VERSION_ALIASES[next_tok]
            i += 2
        elif next_tok and "/" in next_tok and not repo_path_exists(next_tok):
            REVIEW_MODEL = next_tok
            i += 2
        else:
            i += 1
        continue

    if tok in {"agent:orion", "agent:bard"}:
        stop and report: "{tok} is a reserved stub pending AGENTS.md landing
        and empirical validation that persona boot adds value beyond
        team-reviewer with a generic reviewer prompt."

    if "/" in tok and not repo_path_exists(tok):
        REVIEW_MODEL = tok
        REVIEW_MODE = "team-reviewer"
        BOOT = "mcp"
        i += 1
        continue

    ask for clarification

if SINCE_ARG in {"penultimate commit", "the penultimate commit", "second-to-last commit"}:
    SINCE_REF = "HEAD~1"
else:
    SINCE_REF = SINCE_ARG
```

**Model resolution invariant** (shared with `/session-review`): omitted args →
**`web-claude`**. Any token that resolves to a concrete model id
(`openai/gpt-5.5`, `anthropic/claude-opus-4-7`, etc.) uses **`team-reviewer`**
unless `team-inline`/`raw`, `web-claude`, `claude-web`, `claude-cursor`, or
`--grok` was explicitly selected.
This ensures MCP-grounded frontier review when a model token is supplied.

Report the resolved review mode, model/agent, path override (if any), and
`since` ref (if any) before proceeding.

### 1. Establish Scope

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHA=$(git rev-parse --short HEAD)
```

If `SINCE_REF` is set, you are in **explicit since mode**.
If only `PATH_ARG` is set, you are in **path-filtered git-status mode**.
Otherwise you are in **git-status mode**.

In git-status mode:
- run `git status --short`
- include files from `git status` that are modified, added, or untracked
- exclude pure deletions from review
- include untracked files only if they are source files or otherwise meaningful
  to behavior (config, infra, schema, prompts, docs that materially affect the change)

In path-filtered git-status mode:
- run `git status --short`
- build the normal candidate list, then filter it to `PATH_ARG`

In explicit since mode:
- if `SINCE_REF` is set, validate it first: `git rev-parse --verify "$SINCE_REF"`
- if `PATH_ARG` is set, restrict review to that path
- review the **current file state** for files changed between `SINCE_REF` and `HEAD`
- pure deletions are still excluded from review

Report: branch name, `HEAD` sha, selection mode, files changed, total ± lines.

**Stop if scope is empty.**
- Git-status mode: "No modified, added, or meaningful untracked files are currently reported by `git status`."
- Path-filtered git-status mode: "No modified, added, or meaningful untracked files under `{PATH_ARG}` are currently reported by `git status`."
- Explicit selection mode: "No reviewable files matched `{PATH_ARG}` since `{SINCE_REF}`."

**If > 150 files changed**: warn the user and offer to review a focused
subset (e.g. by directory). Do not proceed silently with an overwhelming diff.

### 1a. Build File List and SLOC Gate

Identify reviewable files, then partition them into source / non-source /
auto-excluded.

```bash
if [ -n "$SINCE_REF" ]; then
  COMPARE_REF="$SINCE_REF"
  if [ -n "$PATH_ARG" ]; then
    CANDIDATE_FILES=$(git diff --name-only --diff-filter=d "$COMPARE_REF" HEAD -- "$PATH_ARG")
    DELETED=$(git diff --name-only --diff-filter=D "$COMPARE_REF" HEAD -- "$PATH_ARG")
  else
    CANDIDATE_FILES=$(git diff --name-only --diff-filter=d "$COMPARE_REF" HEAD)
    DELETED=$(git diff --name-only --diff-filter=D "$COMPARE_REF" HEAD)
  fi
  STATUS_LINES=""
else
  # Porcelain statuses:
  #   M/A = tracked modifications/additions (staged or unstaged)
  #   ??  = untracked
  #   D   = pure deletion (exclude from review)
  STATUS_LINES=$(git status --porcelain=v1)

  TRACKED_CHANGED=$(printf '%s\n' "$STATUS_LINES" \
    | awk 'substr($0,1,2) != "??" && index(substr($0,1,2), "D") == 0 && substr($0,4) != "" {print substr($0,4)}')

  UNTRACKED=$(printf '%s\n' "$STATUS_LINES" \
    | awk 'substr($0,1,2) == "??" {print substr($0,4)}')

  CANDIDATE_FILES=$(printf '%s\n%s\n' "$TRACKED_CHANGED" "$UNTRACKED" | sed '/^$/d' | sort -u)

  DELETED=$(printf '%s\n' "$STATUS_LINES" \
    | awk 'index(substr($0,1,2), "D") > 0 && substr($0,4) != "" {print substr($0,4)}')

  if [ -n "$PATH_ARG" ]; then
    CANDIDATE_FILES=$(printf '%s\n' "$CANDIDATE_FILES" \
      | awk -v p="$PATH_ARG" '$0 == p || index($0, p "/") == 1')
    DELETED=$(printf '%s\n' "$DELETED" \
      | awk -v p="$PATH_ARG" '$0 == p || index($0, p "/") == 1')
  fi
fi

# Auto-exclude patterns (lock files, generated artifacts, source maps)
EXCLUDE_RE='(package-lock\.json|yarn\.lock|\.lock$|\.min\.(js|css)$|\.map$|go\.sum$)'

# Source files (reviewed by default — test files INCLUDED on purpose:
# they enforce invariants too and frequently violate them silently)
SOURCE_RE='\.(py|js|ts|jsx|tsx|php|go|rs|java|rb|c|cpp|h|cs|swift|kt)$'
SOURCE_FILES=$(echo "$CANDIDATE_FILES" | grep -E "$SOURCE_RE" | grep -v -E "$EXCLUDE_RE")

# Non-source candidates (config, infra, docs — agent picks at step 3)
NONSOURCE_FILES=$(echo "$CANDIDATE_FILES" | grep -v -E "$SOURCE_RE" | grep -v -E "$EXCLUDE_RE")

# Auto-excluded (track for the prompt's <excluded_files> block)
AUTO_EXCLUDED=$(echo "$CANDIDATE_FILES" | grep -E "$EXCLUDE_RE")
```

**Track an excluded list** — every file you do not send to the model goes
here, with a one-word reason. This becomes `{EXCLUDED_FILES}` in step 4:
- All files in `$AUTO_EXCLUDED` → reason: `generated`
- All files in `$DELETED` → reason: `deletion` (pure delete from `git status`; not reviewed)
- Any non-source file you choose not to include in step 3 → reason: `non-source`
- Any source file omitted by user choice in the SLOC gate → reason: `sloc-skip`

Format one per line: `path/to/file — reason`.

**SLOC gate** — for every file in `$SOURCE_FILES`, check its current line
count and whether it existed at the comparison baseline (`HEAD` in git-status
mode; `SINCE_REF` in explicit since mode):

```bash
SLOC_VIOLATIONS=""
BASELINE_REF="${SINCE_REF:-HEAD}"
for f in $SOURCE_FILES; do
    [ -f "$f" ] || continue
    lines=$(wc -l < "$f")
    if git cat-file -e "${BASELINE_REF}:$f" 2>/dev/null; then
        # Existed at baseline — existing-file limit
        [ "$lines" -gt 400 ] && \
            SLOC_VIOLATIONS+="  $f — $lines lines (existing, limit 400)\n"
    else
        # New relative to baseline — new-file limit
        [ "$lines" -gt 300 ] && \
            SLOC_VIOLATIONS+="  $f — $lines lines (new file, limit 300)\n"
    fi
done
[ -n "$SLOC_VIOLATIONS" ] && printf "$SLOC_VIOLATIONS"
```

If `$SLOC_VIOLATIONS` is non-empty, **stop and prompt the user**:

```
The following files exceed SLOC limits:
{violations}

Options:
  1. Address SLOC violations first, then rerun /diff-review
  2. Continue review as-is (violations will appear as Critical findings)
```

Wait for user choice. Do not proceed silently.

### 2. Build `<invariants>` Block

The reviewing model does not see Cursor `.mdc` auto-load. Canonical layers live
in cortex agent-skills (post skill-migration). Choose the branch by `REVIEW_MODE`.

#### 2m. MCP-capable modes (`team-reviewer`, `web-claude`, `grok-build`)

Use `architecture-handoff-protocol.mdc` § "Block 2: `<invariants>`" — MCP-equipped
dispatchers template. **Do not** re-inline the universal or ULG layers (stale
duplicate); the reviewer fetches them via MCP.

Before writing per-task narrowing lines, read both skills (dispatcher packet-build):

```
fs(sandbox="cortex", op="read", path="agent-skills/architecture-invariants.md")
fs(sandbox="cortex", op="read", path="agent-skills/ulg-architecture.md")
```

Paste into the packet (substitute task-scoped narrowing — ≤15 lines, source-tagged):

```
<invariants>
Read the current architecture invariants before forming findings:
- fs(sandbox="cortex", op="read", path="agent-skills/architecture-invariants.md")
  — universal layer
- fs(sandbox="cortex", op="read", path="agent-skills/ulg-architecture.md")
  — ULG-specific layer (read when the task touches universal-llm-gateway)

Per-task narrowing (read the skills above first; these are emphases for THIS
task, not the full invariant set):

[task-scoped lines — e.g. [workspace:topology], [universal:no-bc] if deletions]
</invariants>
```

**Deletion-task callout**: if the review corpus deletes an API, MCP tool, route,
signal, or other consumer-visible surface, the narrowing MUST call out
`[universal:no-bc]` and `[docs:no-vestigial]` (both live in
`architecture-invariants.md`). Omission is a packet bug.

#### 2r. Non-MCP modes (`frontier`, `team-inline`)

No fetch surface for the reviewer. Build a composed inline stack (≤50 lines,
source-tagged) per `architecture-handoff-protocol.mdc` § "Block 2" — non-MCP
dispatchers. First read both cortex skills (dispatcher packet-build):

```
fs(sandbox="cortex", op="read", path="agent-skills/architecture-invariants.md")
fs(sandbox="cortex", op="read", path="agent-skills/ulg-architecture.md")
```

Then compose universal + workspace + task layers into `<invariants>` — include
at minimum `[universal:no-bc]` and `[docs:no-vestigial]` when the corpus involves
deletions. Cover actionable constraints from those skills and any loaded
`{workspace}/.cursor/rules/*_ws.mdc` relevant to the changed files (transport,
events, API namespace, code scope, quality gates, topology). One line per invariant:

```
[transport] ¬ httpx.AsyncClient(transport=...) directly — use make_async_client()
[events]    ¬ Event() construction — use @event_factory decorated function
...
```

### 3. Build Review Corpus or Manifest

Raw frontier mode needs a full inlined source corpus because the reviewer has
no tools. MCP-capable modes (`team-reviewer`, `web-claude`, and future verified
agent modes) need a manifest packet: the review contract plus file paths,
changed-symbol hints, and line counts. MCP reviewers MUST read live source files
via `fs` and treat the packet as dispatch metadata, not source evidence.

The review list is `$SOURCE_FILES` from step 1a. **At your discretion**, also
append entries from `$NONSOURCE_FILES` if they are < 100 lines and have
meaningful changes (config that drives behavior, infra, schema, prompts).
Every non-source file you skip goes in the excluded list with reason
`non-source`.

Annotate each file header with the **changed symbols** extracted from the diff
hunk context lines (`@@ ... @@ def foo():` / `class Bar:`). This tells the
model exactly where to focus without changing what it sees.

For all modes, build `REVIEW_MANIFEST`:

```bash
COMPARE_REF="${SINCE_REF:-HEAD}"
REVIEW_FILES="$SOURCE_FILES $CHOSEN_NONSOURCE"  # CHOSEN_NONSOURCE = your picks
REVIEW_MANIFEST=""
for f in $REVIEW_FILES; do
    [ -f "$f" ] || continue
    lines=$(wc -l < "$f")

    # Extract the function/class context from each changed hunk (@@ lines)
    # plus any top-level def/class lines that appear as additions.
    # Combines: (1) nearest enclosing symbol for modified hunks,
    #           (2) newly added top-level symbols not visible in @@ context.
    changed=$(
        {
            git diff "$COMPARE_REF" -- "$f" \
                | grep '^@@' \
                | sed 's/^@@ [^ ]* [^ ]* @@ //'
            git diff "$COMPARE_REF" -- "$f" \
                | grep '^+[^+]' \
                | grep -E '^\+(def |class )[A-Za-z_]' \
                | sed 's/^+//'
        } \
        | grep -oE '(def |class )[A-Za-z_][A-Za-z0-9_]*' \
        | sed 's/^def //; s/^class //' \
        | sort -u \
        | tr '\n' ', ' \
        | sed 's/, $//'
    )

    if [ -n "$changed" ]; then
        header="=== $f ($lines lines) | changed: $changed ==="
    elif ! git cat-file -e "${COMPARE_REF}:$f" 2>/dev/null; then
        # Untracked/new file — no diff context, model should read all of it
        header="=== $f ($lines lines) | new file ==="
    else
        # Existing file with only module-level changes (imports, constants,
        # decorators) — no enclosing def/class for the diff to attach to
        header="=== $f ($lines lines) | changed: <module-level> ==="
    fi

    REVIEW_MANIFEST="${REVIEW_MANIFEST}"$'\n'"$header"
done
printf '%s\n' "$REVIEW_MANIFEST"
```

Only in raw frontier mode, build `REVIEW_CODE` by appending current file
contents under the same headers:

```bash
REVIEW_CODE=""
while IFS= read -r header; do
    [ -n "$header" ] || continue
    f=$(printf '%s\n' "$header" | sed -E 's/^=== ([^ ]+) .*$/\1/')
    [ -f "$f" ] || continue
    REVIEW_CODE="${REVIEW_CODE}"$'\n'"$header"$'\n'"$(cat "$f")"$'\n'
done <<EOF
$REVIEW_MANIFEST
EOF
printf '%s\n' "$REVIEW_CODE"
```

For `team-reviewer` and `web-claude`, do **not** inline file contents in the
packet. Include the manifest and instruct the reviewer to read each listed
file via:

```text
fs(sandbox="workspaces", op="read", path="<repo>/<path>")
```

The manifest's `HEAD` is the canonical review boundary. Live workspace files
read via `fs` are authoritative for current state; if they appear to drift from
the manifest's branch/head/diffstat, surface that discrepancy for triage rather
than silently redefining the review scope.
MCP manifest packets MUST include:

```text
<review_manifest>
{REVIEW_MANIFEST}
</review_manifest>
```

MCP manifest packets MUST NOT include a `<code>` block with file contents.

### 3a. Manifest Expansion

Run this step when `REVIEW_MODE` is `"team-reviewer"` or `"grok-build"`. Skip
for raw `frontier` and `web-claude` modes — web-claude discovers context via
its own MCP tool calls; raw frontier receives the full inlined corpus.

The Cursor agent (the dispatching agent — not a subagent) runs targeted grep
searches to find files likely relevant to the review but not in the git diff
scope.

For each changed symbol in `REVIEW_MANIFEST`, search for:

**1. Downstream callers** — who calls the changed functions/classes:

```bash
# For each exported function or class name in "changed:" annotations:
grep -r "symbol_name" libs/ services/ --include="*.py" -l
# Exclude the source file itself from results
```

**2. Test files** — tests that exercise the changed code (catches tests NOT
in the diff):

```bash
grep -r "import.*module_name\|from.*module_name" tests/ libs/ services/ \
    --include="test_*.py" -l
```

**3. Contract consumers** — if changed code touches a data structure or dict
key by name (e.g. "exhaustion_summary", "distinct_turns", "failed_tools"),
grep for those strings across the codebase:

```bash
grep -r "key_name" services/ libs/ --include="*.py" -l
```

**4. Downstream event handlers** — if changed code emits events, find subscribers:

```bash
grep -r "signal_name\|event_name" services/ libs/ --include="*.py" -l
```

For each file found by the expansion search:
- If it is a source file **NOT** already in the manifest: add it with a
  `| expanded: <reason>` annotation (e.g. `| expanded: caller of classify_tool_failure`)
- If it is already in the manifest: skip
- If more than 5 expansion files are found: keep the 5 most relevant by
  proximity to the changed symbols; note the cap in the report

**Cap total manifest size**: expansion + original ≤ 15 files. If the
expanded manifest would exceed 15 files, present the list to the user and
ask which to include before proceeding.

Report: `Expanded manifest: +N files added (callers: X, tests: Y, contract consumers: Z, event handlers: W)`

Carry forward `EXPANDED_FILES` (files added by expansion) for use in the
ESCALATE_TO_WEB gap predicate after primary dispatch.

### 4. Dispatch the Review

| `REVIEW_MODE` | Section | Notes |
|---|---|---|
| `web-claude` / `cursor-claude` | 4w | `team_dispatch(op=handoff)` → manual seat (default web) |
| `team-reviewer` | 4m | `team_dispatch` reviewer role + MCP tools + expansion; claude-web fallback (gap-triggered) |
| `grok-build` | 4g | grok-build dispatch + expansion; claude-web fallback (truncation / `--ab`) |
| `team-inline` / `frontier` | 4f | `role=synthesizer`, no MCP tools, packet inlined |
| `agent:orion` / `agent:bard` | 4x | reserved stubs |

### 4f. Inline Team Review (`team-inline`)

Call `team_dispatch` with:
- `op="generate"`, `role="synthesizer"`
- `dispatch_thread_id=<stable arc id>`
- `model=REVIEW_MODEL` (optional override within synthesizer `allowed_models`)
- `reasoning_effort="high"`, `caller_agent="cursor"`

Build the prompt from the template below, substituting
`{INVARIANTS}` from step 2r, `{CODE}` from `REVIEW_CODE` in step 3,
`{BRANCH}` from step 1, `{EXCLUDED_FILES}` from step 1a, and
`{SINCE_REF_OR_STATUS_MODE}` from step 1:

---
```
You are reviewing source code for invariant violations, correctness, and
quality-gate compliance. The files below are the **current state** of the
selected files under review — not a diff. Pure deletions are intentionally
excluded.

Selection mode:
- default: files currently reported by `git status` as modified, added, or meaningful untracked files
- path-filtered override: only matching files from the current `git status` set
- explicit override: files selected by path and/or `since` ref

Each file header includes a `| changed: ...` annotation listing the
functions and classes that were modified or added on this branch. Start your
analysis there — the rest of the file is available for context but those
symbols are where the changes live.

The workspace invariants below are hard rules. Violations are Critical
findings regardless of whether the code "works". Your primary job is to find
violations — not to praise correct code.

If you need to examine a related file that is not included here (e.g. a base
class, a dependency, an imported module), list it inside a <need> tag:
  <need>path/to/file.py</need>
Place all <need> tags before your findings. The reviewing agent will fetch
those files and give you a second pass with the additional context.

<invariants>
{INVARIANTS}
</invariants>

<excluded_files reason="pure deletions, generated, or non-source">
{EXCLUDED_FILES}
</excluded_files>

<branch>{BRANCH}</branch>
<since>{SINCE_REF_OR_STATUS_MODE}</since>

<code>
{CODE}
</code>

<task_guidance>
{Embed verbatim from skill — read at packet-build time:}
fs(sandbox='workspaces', op='md_read', path='universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md', section='Code Review Dimension')
fs(sandbox='workspaces', op='md_read', path='universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md', section='Discipline')
{Concatenate Code Review Dimension + Discipline into this block.}
</task_guidance>

Format every finding using the v1 structured shape per
`architecture-handoff-protocol.mdc` § "Block 6: <output_format>" → §
"Compact inline form for non-MCP dispatchers". Key fields:

  FindingID:    F<n>                  (stable, sequential)
  Severity:     Critical | Warning | Suggestion
  File:         path/to/file
  FileReadVia:  fs | absolute | not_read
  Concern:      <paragraph; cite invariant tag>
  Operation:    replace | create_file | delete_file | delete_substring |
                replace_whole_file | replace_all_occurrences |
                plan_required | needs_info | deferred | blocked
  DependsOn:    F<m>[resolved | applied | approach=A]  (semantic only)

For mechanical Operations, include an Edits block (column-0 SEARCH/REPLACE
fences, Occurrence=exactly_once, ApplyAfter) and a Verify list (commands
must match the closed Verify Allowlist: pytest, ruff check, ruff format,
ruff check --select=UP --fix, mypy, python -m, npm test, cargo check,
wc -l, rg).

For paused Operations, include required subfields per the protocol:
  - plan_required: Scope, AffectedFiles, InvariantBroken, WhyPatchIsUnsafe,
    MinimalPlan, AcceptanceChecks
  - needs_info:    at least one of WouldFetch or Questions
  - deferred:      Options ≥2 with tradeoffs, RecommendedNext
  - blocked:       BlockedReason (closed enum), UnblockedBy (action-verb)

Reviewer MUST NOT emit: NewlineMode, FileSha256Before, ExpectedCount.

Return ONLY findings (and any <need> tags) — no summaries, no praise,
no filler. If you find nothing, return exactly: "No findings."
```
---

`team_dispatch` returns a **dispatch envelope** with an `execution_id`,
not the model output. Extract the ID and poll via the `pipeline` MCP tool:

```
EXEC_ID = <from team_dispatch response>
result = pipeline(op="result", execution_id=EXEC_ID, wait_seconds=60)
```

`wait_seconds` is a server-side short-poll (clamped to 60s). Re-poll up to
5 × 60 s (5-minute ceiling). The result JSON has shape:

```json
{
  "status": "completed",
  "result": {
    "content": "<model output here>",
    "model_used": "openai/gpt-5.4",
    ...
  }
}
```

`status` ∈ {`pending`, `running`, `completed`, `failed`}. The model output is
in `result.content`; the effective model is in `result.model_used` (or the
provider-specific field returned by the pipeline). If still pending after
5 min, save the `execution_id` and stop — user can resume by re-polling
the same `execution_id`.

If `status == "failed"`, stop and surface `result.error` — do not
silently fall back to a local review.

### 4m. Frontier-MCP Dispatch

Use this path when `REVIEW_MODE == "team-reviewer"`. The frontier model
dispatches with `boot="mcp"` (persona-free system prompt) and a curated
tool list. Investigation is part of the review job — the model can read
live source files via `fs`, verify failure-mode claims with
`observability`, and ground findings with `cortex`/`rag` queries before
producing them.

Build the same manifest packet content as step 4w (`<invariants>` from step 2m,
`<task_guidance>` from `.cursor/skills/review-task-guidance/SKILL.md` (Code Review
Dimension + Discipline only), `<mcp_capabilities>` adapted, `<output_format>`, and
`REVIEW_MANIFEST`). Two adaptations from the web-claude packet:

1. **`<mcp_capabilities>` block** — drop the `agent_bus` reference. The
   team-reviewer reviewer cannot reply to a thread; results return through
   the pipeline poll. Replace with a sentence reminding the model to
   inline cited evidence in each finding.
2. **Packet partition** — system prompt carries the durable role contract
   (invariants, MCP capabilities, output format); user message carries the
   per-pass manifest + scope metadata. This separates framing from the live
   source list so the same system prompt can be reused across re-dispatch
   passes.

#### Frontier-MCP Review Artifact Boundary

If the manifest packet is staged as a workspace file because it is too large to
inline, that packet path is a **transport artifact**, not review evidence. The
reviewer MUST be told:

- read only the exact packet path supplied by the dispatcher
- do not `list`, `search`, or broad-read `tmp/reviews`
- do not read sibling review packets, summaries, or prior review artifacts
- use `cortex` / `rag` for prior decisions and existing patterns, not
  stale `tmp/reviews` files
- after reading the packet, use `fs` only for live source files named in the
  changed-file list or their direct dependencies needed to substantiate a
  finding

This prevents stale or unrelated review artifacts from becoming false context.

Dispatch:

```
EXEC = team_dispatch(
    op="generate",
    role="reviewer",
    dispatch_thread_id=f"diff-review-{BRANCH}",
    messages=[{"role": "user", "content": <packet body — manifest + metadata>}],
    model=REVIEW_MODEL,            # optional; default from reviewer role
    system=<packet system — role + invariants + mcp capabilities + output format>,
    reasoning_effort="high",
    caller_agent="cursor",
    max_tool_turns=100,            # Stargate default of 10 is too low for review-grade investigation; see dogfood-notes
)
EXEC_ID = EXEC["execution_id"]
```

Poll the result via `pipeline(op="result", execution_id=EXEC_ID,
wait_seconds=60)` (server-side short-poll, clamped to 60s). Re-poll up to
5 × 60s. Result JSON has the standard shape `{status: "completed",
result: {content, model, usage, ...}}`.

#### Failure handling

| Failure | Detection | Reaction |
|---|---|---|
| Stargate transport error before dispatch | `team_dispatch` raises | Stop. Offer (1) start Stargate and rerun, (2) fall back to `web-claude`. |
| Tool-resolution error (a tool name is unknown to the live MCP catalog) | `result.error` references the unresolved tool name | Stop and report `result.error` verbatim. Most likely cause: `MCP_PUBLIC_URL` / `MCP_AUTH_TOKEN` not configured on Stargate. Fall back to `web-claude` after surfacing. |
| Tool-loop exhausted (model used all `max_tool_turns` and never returned findings) | `status: completed`, `result.content == ""`, `usage.completion_tokens > 0`. Confirm via `observability(operation="pipeline-trace", params={"execution_id": EXEC_ID})` — exhaustion emits `pipeline.frontier.dispatch.exhausted` as the terminal pre-completion signal. | Re-dispatch the same packet with `generation_options={"max_tool_turns": 25}` (or higher). Default cap of 10 is insufficient for review-grade reasoning over many source files — the dogfood pass needed 25. Only after a second exhaustion should the artifact note `Model: <id> (tool-loop exhausted twice)` and surface to the user. |
| `status: failed` after dispatch | `status` field of poll result | Report `result.error` verbatim. Offer fall-back to `web-claude`. |

#### Iteration in team-reviewer mode

Frontier-mcp does not support multi-turn dialectic the way `web-claude`
does — Stargate's tool loop terminates when the model returns content
with no further tool calls. For iterative reviews:

1. First pass: dispatch as above → poll → findings.
2. Validate + triage findings (step 5), apply Critical fixes.
3. Second pass: rebuild the manifest packet with the current file list and a
   "Prior pass findings resolved:" preamble in the user message. Tell the
   reviewer to re-read live source files via `fs`, then re-dispatch with a fresh
   `team_dispatch` call. The model has no inherent memory of the prior pass;
   the dispatcher carries continuity.
4. Repeat until the reviewer returns "No findings" or only suggestions.

Skip step 4a entirely — `<need>` semantics are a web-claude affordance.
The team-reviewer reviewer fetches files itself via `fs`.

#### Adapted `<mcp_capabilities>` block (paste into packet)

```
<mcp_capabilities>
You have MCP tools. Investigation is part of the review job — do not
limit yourself to the manifest below.

Tool-turn budget: you have up to 100 tool turns. The budget counts turns,
not individual tool calls — multiple tool calls issued in a single turn
cost only one turn. When reading source files, issue parallel
fs(sandbox="workspaces", op="read", ...) calls within a single turn
(5–10 files per turn) rather than one file per turn. Reason over the
batch before reading the next. If a tool call fails, adapt or skip —
retrying the same failed call wastes another turn.

Review artifact boundary:
- `tmp/reviews` contains transport artifacts and stale prior reviews, not
  authoritative source evidence.
- If the dispatcher gives you a packet path under `tmp/reviews`, read exactly
  that file and no sibling files.
- Do not `fs list`, `fs search`, or broad-read `tmp/reviews`.
- Use `cortex` / `rag` for prior decisions and existing patterns.
- Use `fs` after packet intake only for live source files named in the
  changed-file list, or direct dependencies needed to substantiate a finding.

Before forming findings, ground them by:

0. fs(sandbox="cortex", op="read", path="agent-skills/architecture-invariants.md")
   AND fs(sandbox="cortex", op="read", path="agent-skills/ulg-architecture.md")
   — read BEFORE forming findings. The packet's <invariants> block carries
   per-task narrowing only; the skills carry the canonical universal + ULG layers.

1. fs(sandbox="workspaces", op="read"|"list"|"md_read", path="<repo>/<path>")
   — read each manifest-listed source file and any direct dependency needed to
   substantiate a finding. Live files represent current state; the manifest's
   branch/head remains the review boundary.
   Use op="md_read" with a "section" arg for large markdown docs.

2. cortex(tool="search", arguments='{"query": "<terms>", "limit": 10}')
   cortex(tool="entity_get", arguments='{"entity_id": "<id>"}')
   — surface prior decisions, observations, or todos touching the same
   subsystem. Flag conflicts with prior conclusions explicitly.

3. observability(operation="recent-failures", params={"limit": 20})
   observability(operation="pipeline-trace", params={"execution_id": "<id>"})
   — verify the failure modes the change claims to fix are observable in
   production events. If the symptom is hypothetical, say so.

4. rag(op="search", arguments='{"query": "<terms>", "scope": "code"}')
   — find existing patterns the change may be reinventing.

Cite each tool call inline with the relevant finding so the dispatching
agent can audit the evidence trail:

  Evidence: fs read agent-skills/architecture-invariants.md → [universal:no-bc] applies
  Evidence: cortex search "FederationCircuitBreaker eviction" → 0 results
  Evidence: observability recent-failures → 12 events matching pattern
  Evidence: fs read services/rag/contextualize.py:154 → confirms snippet

You cannot post replies to a thread or use <need> tags — return all
findings inline with their evidence in your final response.
</mcp_capabilities>
```

#### Adapted `<output_format>` block for team-reviewer (paste into packet)

The team-reviewer variant uses the v1 structured shape per
`architecture-handoff-protocol.mdc` § "Block 6: `<output_format>`" → § "v1
Code Finding / Session Finding". `Evidence:` is mandatory in this mode —
citations are required since there is no follow-up thread to ask for them
later.

```
<output_format>
Use the v1 structured shape per `architecture-handoff-protocol.mdc` §
"Block 6: <output_format>". Required fields per finding:

  FindingID, Severity, File, FileReadVia, Concern, Evidence (REQUIRED in
  this mode), Operation, optional DependsOn.

For mechanical Operations (replace / create_file / delete_file /
delete_substring / replace_whole_file / replace_all_occurrences), include
an Edits block (column-0 SEARCH/REPLACE fences, Occurrence, ApplyAfter)
and a Verify list (commands from the closed Verify Allowlist).

For paused Operations (plan_required / needs_info / deferred / blocked),
include the required subfields per § "Paused operations".

Citations are mandatory — use the citation forms shown in the
<mcp_capabilities> block.

Reviewer MUST NOT emit: NewlineMode, FileSha256Before, ExpectedCount.

Return ONLY findings — no summaries, no praise, no filler. Do NOT use
<need> tags. If you find nothing, return exactly: "No findings."
</output_format>
```

#### Cognitive Fallback After Frontier-MCP

After polling results, evaluate the gap predicate before moving to triage:

```python
manifest_files = set(f for f in REVIEW_MANIFEST.split() if f.endswith(".py"))
gap_files = set()
for finding in parse_findings(result_content):
    if finding.severity in ("Critical", "Warning"):
        referenced = extract_file_refs(finding)
        gap_files |= referenced - manifest_files

ESCALATE_TO_WEB = len(gap_files) > 0
```

If `ESCALATE_TO_WEB = True`: build a claude-web packet (4w format) that
includes the gap files with `| escalation: referenced in team-reviewer finding`
annotations. Hand off to `claude-web` via `team_dispatch(op="handoff", role="claude-web", …)`.
Note which findings prompted
the escalation in the thread body. Do NOT escalate for Suggestion-only findings.

Report: `ESCALATE_TO_WEB = True — gap files: {list}` or `ESCALATE_TO_WEB = False`.

### 4g. Grok-Build Dispatch

Use this path when `REVIEW_MODE == "grok-build"` (i.e., `--grok` flag was
supplied). Step 3a must have run before this step.

Build a grok-build packet following the non-MCP dispatcher format (two
sections: `[SYSTEM_CONTEXT]` and `[PROMPT]`):

```bash
mkdir -p tmp/reviews
PACKET="tmp/reviews/${BRANCH//\//-}-grok-diff-review-packet.md"
SUMMARY="tmp/reviews/${BRANCH//\//-}-grok-diff-review-summary.md"
```

**`[SYSTEM_CONTEXT]`** — `<invariants>` from step 2m (skill refs + per-task
narrowing; grok-build subprocess reads skills via `fs`), task guidance (read from
`.cursor/skills/review-task-guidance/SKILL.md` — Code Review Dimension + Discipline
only; embed verbatim), output format (no `Evidence:` field).

**`[PROMPT]`** — branch, HEAD, diffstat, selection mode, `REVIEW_MANIFEST`
(with expansion annotations) plus absolute-path read instructions:

```
Read each listed file directly at its absolute path before forming findings:

  /mnt/torus/projects/universal-llm-gateway/<path>
```

Dispatch:

```python
RESULT = grokbuild(
    op="build",
    cwd="/mnt/torus/projects/universal-llm-gateway",
    mode="read_only",
    system_context=<contents of [SYSTEM_CONTEXT] section from packet>,
    prompt=<contents of [PROMPT] section from packet>,
)
DISPATCH_ID = RESULT["dispatch_id"]
```

Immediately after dispatch, check:

1. `RESULT["status"]` — if not `"completed"`, report the failure verbatim
   and offer to fall back to `web-claude`.
2. `RESULT["metadata"]["read_only_violation"]` — if `True`, surface as a
   warning: grok performed unexpected writes in read-only mode.
3. `RESULT["metadata"].get("truncated")` — if `True`, note stdout truncation;
   this automatically triggers `ESCALATE_TO_WEB`.

**Evaluate `ESCALATE_TO_WEB`**:

```python
# Grok-build subprocess seat has mcp_allowed_read_only — it resolves
# manifest gap files itself via fs(). Gap-files escalation is obsolete
# for grok-build; only AB_MODE and stdout truncation drive escalation.
stdout_truncated = bool(RESULT["metadata"].get("truncated"))
has_non_suggestion = (
    "Severity:     Critical" in RESULT["stdout"]
    or "Severity:     Warning" in RESULT["stdout"]
)
ESCALATE_TO_WEB = (
    AB_MODE
    or (stdout_truncated and has_non_suggestion)
)
```

Report: `dispatch_id`, `status`, packet path, `ESCALATE_TO_WEB` predicate,
sidecar path for full trace inspection.

**When `ESCALATE_TO_WEB = True`**: build a claude-web packet (4w format).
Hand off via `team_dispatch(op="handoff", role="claude-web", …)`. Note stdout truncation and dispatch_id in
the thread body. Capture `THREAD_ID`. Triage grok-build findings immediately
(step 5); claude-web triage happens when the reply arrives.

**When `ESCALATE_TO_WEB = False`**: proceed to triage (step 5) with
grok-build findings only. No `agent_bus` close needed (no thread).

### 4w. Manual-seat handoff (`claude-web` | `claude-cursor`)

Use when `REVIEW_MODE ∈ {web-claude, cursor-claude}`. Operator may say only
`to claude-web` / `to claude-cursor` — that implies `team_dispatch(op="handoff", …)`.

The receiving seat is MCP-equipped (Cortex, RAG, observability, `agent_bus`, `fs`).
The packet treats those tools as primary investigation surfaces, not fallbacks.

Build the same finding-format and manifest packet described in steps 2m and 3,
but write it to a durable workspace artifact (not `team_dispatch`):

```bash
mkdir -p tmp/reviews
SEAT_SUFFIX="claude-web" if REVIEW_MODE == "web-claude" else "claude-cursor"
PACKET="tmp/reviews/${BRANCH//\//-}-${SEAT_SUFFIX}-diff-review-packet.md"
SUMMARY="tmp/reviews/${BRANCH//\//-}-${SEAT_SUFFIX}-diff-review-summary.md"
PACKET_WS="universal-llm-gateway/${PACKET}"
HANDOFF_ROLE = "claude-web" if REVIEW_MODE == "web-claude" else "claude-cursor"
```

The packet MUST include:
- selection mode, branch, `HEAD`, diffstat, reviewed file count
- the `<invariants>` block from step 2m (skill refs + per-task narrowing)
- `<task_guidance>` from `.cursor/skills/review-task-guidance/SKILL.md` (Code Review
  Dimension + Discipline only; read via `fs` `md_read` at packet-build time and embed
  verbatim)
- `<excluded_files>` and SLOC violations
- `REVIEW_MANIFEST` with file paths, line counts, and changed-symbol headers
- a `<mcp_capabilities>` block (template below) listing the available tools,
  the investigation expectation, and the `Evidence:` citation requirement
- the exact finding format and `<need>path</need>` last-resort instructions

#### `<mcp_capabilities>` block (paste into packet)

```
<mcp_capabilities>
You have full MCP tooling. Investigation is part of the review job — do
not limit yourself to the manifest below.

Before forming findings, ground them by:

0. fs(sandbox="cortex", op="read", path="agent-skills/architecture-invariants.md")
   AND fs(sandbox="cortex", op="read", path="agent-skills/ulg-architecture.md")
   — read BEFORE forming findings. The packet's <invariants> block carries
   per-task narrowing only; the skills carry the canonical universal + ULG layers.

1. fs(sandbox="workspaces", op="read"|"list"|"md_read", path="<repo>/<path>")
   — read each manifest-listed source file and any direct dependency needed to
   substantiate a finding. Live workspace files are authoritative for current
   state; the manifest's branch/head is authoritative for the dispatched review
   boundary. Use op="md_read" with a "section" arg for large markdown docs.

2. cortex(tool="search", arguments='{"query": "<terms>", "limit": 10}')
   cortex(tool="entity_get", arguments='{"entity_id": "<id>"}')
   — surface prior decisions, observations, or todos touching the same
   subsystem. Flag conflicts with prior conclusions explicitly.

3. observability(operation="recent-failures", params={"limit": 20})
   observability(operation="pipeline-trace", params={"execution_id": "<id>"})
   — verify that the failure modes the change claims to fix are actually
   observable in production events. If the symptom is hypothetical, say so.

4. rag(op="search", arguments='{"scope": "code", "query": "<terms>"}')
   — find existing patterns the change may be reinventing.

5. agent_bus(tool="fetch", arguments='{"thread": "<N>", "compact": true}')
   — when the change references a discussion thread, fetch it to verify
   the change reflects the conclusion.

Cite each tool call inline with the relevant finding so the dispatching
agent can audit the evidence trail:

  Evidence: fs read agent-skills/architecture-invariants.md → [universal:no-bc] applies
  Evidence: cortex search "FederationCircuitBreaker eviction" → 0 results
  Evidence: observability recent-failures → 12 events matching pattern
  Evidence: fs read services/rag/contextualize.py:154 → confirms snippet

Use <need>path</need> ONLY as a last resort — when a file is outside the
"workspaces" sandbox or direct `fs` access fails. Prefer direct fs reads.
</mcp_capabilities>
```

#### Hand off via `team_dispatch`

Write the full packet first (step 4w above). Stargate posts the pointer turn
(≤25 lines) — ¬ `agent_bus(tool="post")` with inlined packet content.

```
HANDOFF = team_dispatch(
    op="handoff",
    role=HANDOFF_ROLE,
    packet_path=PACKET_WS,
    subject=f"Diff review handoff — {BRANCH} @ {HEAD_SHA}",
    caller_agent="claude-cursor",
    tags=[f"project:{REPO_NAME}", "type:review", f"agent:{HANDOFF_ROLE}", "mode:diff-review"],
)
THREAD_ID = HANDOFF["thread_id"]
TO_AGENT = HANDOFF["to_agent"]
POLL_HINT = HANDOFF["poll_hint"]
```

Close your turn with `HANDOFF["push_reminder"]`. `claude-web` → operator push;
`claude-cursor` → open thread in Cursor (Opus optional).

This is a manual async workflow:
1. After handoff, stop and report `THREAD_ID`, `TO_AGENT`, packet path, summary path.
2. Poll: `agent_bus(tool="wait", arguments=POLL_HINT["arguments"])`. When complete,
   inspect the reply before acknowledging or closing.
3. If the reply contains `<need>` tags, resume at step 4a.
4. If the reply contains findings, a sidecar URI, "Review complete", or
   "no `<need>` requests", treat that as review output and resume at step 5.
5. Do not close the thread until findings are validated, written to the review
   artifact, and either applied or explicitly deferred by the user.

#### Iterative / multi-turn reviews (no upper bound)

A single review run is rarely one round-trip. Validated runs converge over
several turns of dialectic — reviewer surfaces findings, dispatcher applies
or pushes back, reviewer concedes/refines, etc. Do not cap turns. Iterate
until one of the convergence signals below appears, the user explicitly
caps the loop, or you and the reviewer reach a stable disagreement.

**Convergence signals** (any one closes the loop):
- `No findings.`
- "Review complete."
- "Spec implementation-ready" / "<artifact> implementation-ready"
- Explicit user instruction to close

When the user requests a follow-up pass after applying findings, the
original manifest may be stale. Reply on the same thread with:

- An updated manifest if the reviewed file set changed.
- Explicit instruction to read the **live workspace files** via
  `fs(sandbox="workspaces", op="read", path="<repo>/...")`.
- A pointer to the prior turn listing applied fixes so the reviewer can
  verify each one resolved the original concern.
- The same `<mcp_capabilities>` framing — investigation tools remain in
  scope across turns.

The original `<invariants>`, `<architecture_guidance>` (if present), and
`<output_format>` blocks remain authoritative across all turns. Live workspace
files remain authoritative for current state; the manifest's branch/head remains
authoritative for the review boundary. State this explicitly in each follow-up
body.

**Dialectical pushback is part of the protocol.** When you disagree with
a finding's severity or applicability:
- Say so explicitly and state the reasoning (workspace rule, code reference,
  scope argument).
- Ask the reviewer to confirm, refine, or concede.
- Apply or defer based on the dialectic's outcome, not the first pass alone.

#### Sidecar artifact pattern

When a review pass produces many findings or long evidence trails, the
reviewer may write a sidecar artifact and reference it from the
`agent_bus` reply rather than inlining everything. Acceptable sidecar
locations:

- `cortex://notes/system/threads/<thread-id>-<slug>-pass<N>.md`
- `tmp/reviews/<branch>-claude-web-diff-review-pass<N>-sidecar.md`

When a sidecar is referenced, fetch and parse it the same way you would
parse an inline reply. Treat the agent_bus turn as the index, the sidecar
as the body. Note the sidecar URI in the final review artifact.

### 4x. Agent Stubs

`agent:orion` and `agent:bard` are reserved stubs. Persona boot is deferred
until:

1. AGENTS.md is live in the repo (provides static invariants via `fs` read)
2. Empirical evidence confirms persona boot adds value beyond `team-reviewer`
   (`boot="mcp"`) with a well-crafted generic reviewer system prompt and
   Cortex tool access

Until then, use `team-reviewer openai` or `team-reviewer gemini` for the same
model families without persona implications.

### 4a. Handle File Requests (one additional pass)

For raw frontier mode, parse `<need>` tags out of `result.content`.
For `web-claude` / `cursor-claude`, parse `<need>` tags out of the manual-seat
agent-bus reply.
Use this pattern in either case:

```bash
echo "$CONTENT" | grep -oE '<need>[^<]+</need>' | sed -E 's|</?need>||g'
```

If any are returned:
1. For each path: verify it exists with `[ -f "$path" ]` (skip if missing, note it).
2. Raw frontier: append fetched files to the `<code>` block under a new header
   line: `=== ADDITIONAL (requested by reviewer) ===`, using the same per-file
   format as step 3 (header + body).
3. Manual-seat (`claude-web` / `claude-cursor`): reply on the same thread with the
   additional file paths and tell the reviewer to read them live via `fs`; do not
   inline file contents unless the file is outside the `workspaces` sandbox or
   direct `fs` access failed.
4. Re-dispatch using the same mode: `team_dispatch` for raw frontier mode,
   or an `agent_bus` reply on `THREAD_ID` for manual-seat modes.
5. Use the second-pass result going forward.

**If the second pass fails** (Stargate transport error, `status: failed`,
or 5-min poll ceiling reached): fall back to the **first-pass result** and
note in the artifact:
> Second-pass review (with N requested files) was unavailable: `{reason}`.
> Findings below are from the first pass only; reviewer requested:
> `{list of <need> paths}`.

**Maximum one additional pass.** If the model requests more files after the
second pass, proceed with what's available and note it in the artifact.

### 5. Validate and Triage Findings

The reviewing model is rule-blind. For each finding, classify into one of
four buckets — not just Apply/Reject:

1. **Cross-check** against your fully loaded workspace rules (already in
   context — check both `_ws.mdc` and shared rules).

2. **Reject by rule** — finding contradicts a workspace rule. Examples:
   - Model suggests TCP for an internal hop when the workspace mandates UDS
   - Model calls a `StrEnum` → `str` change "missing enum validation" when
     the workspace rule explicitly requires free-form strings
   - Model flags unrequested cleanup when `change-scope.mdc` prohibits it

3. **Escalate** severity if a workspace rule makes the issue mandatory.
   Example: model rates an underscore-segment signal as Suggestion but the
   event-system rule makes it Critical.

4. **Apply** Critical fixes that survive validation. For Warnings and
   Suggestions, present and wait for user approval.

5. **Surface for triage** — finding is real and survives rule cross-check
   but touches code or scope outside the immediate review slice (e.g. a
   bug in already-shipped Phase 2 code surfaced during a Phase 4 review).
   Do not auto-apply; do not reject. List in the artifact under "Surfaced
   for Triage" and inform the user. Examples:
   - Diff review of `services/foo/` surfaces a bug in `libs/bar/` not
     touched by the diff.
   - Phase review of phase4 surfaces a regression in already-merged phase2 code.

Report: `{N} received → {M} validated ({K} Critical, {L} Warning, {J}
Suggestion) → {X} applied, {Y} rejected by rule, {Z} surfaced for triage,
{W} pending user decision`.

List each rejected finding with the rule name that overrides it. List each
triaged finding with a one-line scope note explaining where it lives.

For every validated finding, track whether applying it requires a manual docs
or contract update. Event signal, payload, semantic, or failure-mode changes
require auditing `docs/event-contracts.md`; API surface and runtime behavior
changes require auditing the relevant docs.

### 5a. Closure Protocol

After all passes converge (per the convergence signals in step 4w), close
the review loop explicitly:

1. **Confirm convergence with the reviewer** — post a final reply on the
   thread acknowledging the convergence signal, summarizing what was
   applied / rejected / surfaced.
2. **Complete the documentation contract audit** — update manual docs for
   applied event/API/runtime contract changes, or record why no docs update was
   needed. In `docs/event-contracts.md`, the catalog table regions are
   generated from `@event_factory` call sites — regenerate via
   `gen-event-catalog`, never hand-edit inside `<!-- GENERATED -->` markers;
   the curated prose outside the markers is hand-authored.
3. **Update the artifact** (step 6) with the final iteration history,
   including the convergence signal.
4. **Close the thread** via `agent_bus(tool="close", arguments='{"thread":
   "<id>", "summary": "..."}')`. The summary should capture state in one
   line (e.g. "Diff review complete; 12 findings applied, 2 surfaced for
   triage").

Do not close the thread before convergence. Stable disagreement is a valid
state — record it in the artifact and surface to the user instead of
closing.

### 6. Write Artifact

```bash
mkdir -p tmp/reviews
```

Write to `tmp/reviews/{BRANCH}-diff-review.md`:

```markdown
# Diff Review: {BRANCH}

**Head**: {HEAD_SHA}  **Date**: {ISO-UTC}
**Model**: {effective model}  **Scope**: {N} files changed, {±lines}
**Excluded**: {excluded file list, or "none"}

## Critical

{findings or "None"}

## Warnings

{findings or "None"}

## Suggestions

{findings or "None"}

## Applied

- {file}:{line} — {one-line fix description}
- (none)

## Rejected by Rules

- {finding summary} — overridden by `{rule-file}`: {one-line reason}
- (none)

## Surfaced for Triage

- {finding summary} — touches `{out-of-scope path}`: {one-line scope note}
- (none)

## Documentation Contract Audit

- `docs/event-contracts.md`: updated | not needed: {reason}
- other docs: {paths} | not needed: {reason}

## Iteration History

- Turn N (reviewer): {one-line summary} (sidecar: {URI if used})
- Turn N+1 (cursor): {applied / pushed back / surfaced — one-line summary}
- ...
- Convergence signal: `{quoted signal from reviewer}` at turn {N}
```

Report the artifact path to the user.

## Rules

- ¬ proceed if diff is empty
- If the user explicitly supplies a file/path and/or `since <ref>`, honor that
  explicit selection instead of defaulting back to `git status`
- ¬ proceed past 150 changed files without user confirmation
- ¬ proceed past SLOC violations without user confirmation (step 1a)
- ¬ apply any fix without showing it first
- ¬ skip invariant validation (step 5) — the model is rule-blind
- ¬ close without a documentation contract audit; `docs/event-contracts.md` is
  manual and must be updated for event contract changes
- Use `reasoning_effort="high"` — this is the primary driver of `Edits:` block
  completeness; lower effort produces significantly more `deferred` pause ops in
  place of concrete patches
- Omitted args → **`web-claude`**; model tokens route to **`team-reviewer`**
- **Model selection by goal** (team-reviewer path only):
  - `openai/gpt-5.5` — maximises ready-to-apply `Edits:` blocks;
    preferred when you want patches you can apply immediately
  - `anthropic/claude-opus-4-7` — deeper architectural critique and stronger
    reasoning on abstract invariant violations; produces more `plan_required`
    pause ops and fewer auto-applicable patches per dispatch
  - `google/gemini-3-pro-preview` — alternative for large-context manifests;
    similar patch-completeness profile to gpt-5.5
  - Any `provider/model` id supported by Stargate is accepted
- Raw frontier mode requires Stargate running; if `team_dispatch` returns a transport error
  (`stargate_unreachable` or similar), do NOT stop — instead offer the user
  two options and wait for a choice:
  1. **Wait / retry** — user will start Stargate, then rerun `/diff-review`
  2. **Local review now** — proceed through steps 2–3 as normal (invariant
     extraction, source file collection), then perform the review yourself
     using your loaded workspace rules. Apply the same finding format and
     validation steps. Note in the artifact: `Model: local (frontier unavailable)`.
- `team-reviewer` requires Stargate AND a configured live MCP catalog
  (`MCP_PUBLIC_URL` / `MCP_AUTH_TOKEN`). On tool-resolution failure, fall
  back to `web-claude` mode (rebuild the packet to disk and post via
  `agent_bus`). Skip step 4a — `<need>` is a web-claude affordance only;
  the team-reviewer reviewer fetches files itself via `fs`.
- `web-claude` does not require Stargate for dispatch, but it does require a
  working manual-seat handoff (`team_dispatch` + packet on disk).
- Default dispatcher is `web-claude` (`team_dispatch` handoff); `agent_bus` thread
  state is the source of
  truth for review status — the summary artifact mirrors it but does not replace it.
- For `team-reviewer`, the artifact's iteration history is the source of truth
  (no thread); list each pass's `execution_id` and outcome.
- `agent:orion` and `agent:bard` are stubs; use `team-reviewer openai` or
  `team-reviewer gemini` for MCP-equipped reviews without persona boot.
- Manifest expansion (step 3a) runs before dispatch for `team-reviewer` and
  `grok-build` modes. It is the Cursor agent's responsibility — not a subagent.
- `ESCALATE_TO_WEB` is evaluated after primary dispatch findings are in hand.
  ¬ escalate for Suggestion-only findings.

## Cognitive Fallback Protocol (claude-web)

Bare `/diff-review` dispatches to claude-web directly (no fallback). claude-web
is also the escalation path when grok-build or team-reviewer primary dispatch
needs a second opinion:

**team-reviewer path:**
- Trigger: Critical/Warning findings reference files outside the expanded manifest
  (gap_files predicate — pending review: weak empirical basis for gap-triggered
  escalation; deeper question routes to todo:review-chain-mode-design)
- Trigger: tool-loop exhausted twice after re-dispatch with higher max_tool_turns
- Do NOT escalate for Suggestion-only findings

**grok-build path:**
- Trigger: stdout truncated and sidecar inspection reveals incomplete findings
  (with Critical/Warning visible pre-truncation)
- Trigger: user adds `--ab` flag explicitly
- Do NOT escalate for file-gap findings — grok resolves gaps itself via `fs()`
  under `mcp_allowed_read_only`; escalating for gap-triggered findings is obsolete
- Do NOT escalate for Suggestion-only findings — those don't warrant async wait
- When escalating due to truncation: include the dispatch_id and
  `audit_fields.sidecar_path` in the thread body
- When `ESCALATE_TO_WEB = False` for `grok-build` mode: ¬ open an `agent_bus`
  thread — no thread to close
