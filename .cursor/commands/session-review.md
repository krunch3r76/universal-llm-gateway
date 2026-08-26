# /session-review

**Recommended reviewer (2026-08-25):** `team_dispatch(model=cdp/opus-5,
purpose=review)` with the session arc + touched files staged to `cortex://`.
Optional — defer when the harvest would block the next attended move; fire in
the background when the seat is conductor / unattended (latency ≠ skip).
Does not restore silent Terra G4. Command default below (`web-claude` handoff)
remains valid; prefer CDP generate when you are choosing.

Session review across three dispatchers — **`web-claude` (default)**,
sync `frontier-mcp` (model-token path), or async grok-build (`--grok`). Covers two
dimensions simultaneously:

1. **Code review**: invariant violations, correctness, quality-gate compliance
   (the diff-review dimension)
2. **Session critique**: problem framing, diagnosis quality, decision soundness,
   alternatives available, missteps in investigation or approach

**Scope source**: `/session-review` derives its file list from the **current
conversation** — what files were actually worked on and why — not from
`git status`. Git is used as a secondary verification step, not the primary
scope source. Use `/diff-review` when you want a git-status/diff-driven code
review without session context.

**web-claude** (default): async multi-turn via `team_dispatch(op="handoff")` →
`claude-web`; full MCP toolset. Best for session critique (Cortex/RAG grounding,
multi-turn dialectic). Packet on disk; Stargate posts a short bus pointer.

**cursor-claude**: same handoff primitive → `claude-cursor` (dedicated Cursor IDE
thread; operator opens the bus thread in Cursor, Opus optional).

**frontier-mcp**: synchronous `frontier_dispatch` with MCP tools
(`fs`, `cortex`, `observability`, `rag`) via `boot="mcp"`. Select by supplying
any resolved model token (`gpt-5.5`, `openai/gpt-5.4`, `gemini`, etc.) or
`frontier-mcp` — same model-resolution contract as `/diff-review`. Default
model when explicitly routed: `openai/gpt-5.5`. claude-web is the cognitive
fallback when Critical/Warning findings reference manifest gap files.

**grok-build** (`--grok`): async (V2 contract), reads files at absolute
paths via `fs()` under `mcp_allowed_read_only`. Runs with manifest expansion
before any claude-web handoff. claude-web is the cognitive fallback when stdout
is truncated (non–Suggestion-only findings) or when `--ab` is set.

Related commands:
- `/diff-review` — code-only diff review (defaults to web-claude; model tokens → frontier-mcp)
- `/diff-review-loop` — automated frontier-mcp multi-pass code review loop
- `/review-apply` — cross-session apply of findings from any review artifact or agent-bus thread

Related skills:
- `.cursor/skills/review-task-guidance/SKILL.md` — shared task_guidance packet content
- `.cursor/skills/multi-model-review/SKILL.md` — adversarial multi-reviewer chain pattern

## When to Use

- After any focused session of work, as a session-close review ritual
- When the solution involved meaningful decisions, pivots, or user corrections
  that warrant retrospective pressure — not just "check the code"
- When you want a reviewer who can ask: "Was this the right problem to solve?
  Was this the right solution? Were better options available?"
- When user redirections or corrections occurred mid-session and you want
  them pressure-tested by an external reviewer

Use `/diff-review` or `/diff-review-loop` when you want a **git-status/diff-driven**
code review without session context. Use `/session-review` after any focused
session — it reads the conversation to determine what was worked on and why,
then gives the reviewer both the code and the session arc to critique.

Use `/session-review` after any focused session — it reads the conversation
to determine what was worked on and why, then gives the reviewer both the
code and the session arc to critique.

Default: **`web-claude`** (async agent_bus, multi-turn). Use a model token or
`frontier-mcp` for sync frontier review. Use `--grok` for grok-build with
claude-web fallback on truncation.

## Invocation

```
/session-review [model] [path] [since <git-ref-or-alias>]
/session-review --grok [--tier {quick|balanced|thorough|max}] [path] [since <git-ref-or-alias>]
/session-review web-claude [path] [since <git-ref-or-alias>]
/session-review claude-web [path] [since <git-ref-or-alias>]
/session-review claude-cursor [path] [since <git-ref-or-alias>]
```

`model` — optional model family, `gpt-5.x` shorthand, or full model ID.
Uses the **same resolution contract as `/diff-review` step 0** (see that
command for the canonical `MODEL_FAMILIES`, `GPT_VERSION_ALIASES`, and
path-before-model disambiguation). Any resolved model id → `frontier-mcp`.

Dispatcher resolution (first matching explicit token wins; else default):

| Token / condition | Dispatcher | Model |
|---|---|---|
| omitted | `claude-web` | `claude-web` |
| `web-claude` / `claude-web` | `claude-web` | `claude-web` |
| `claude-cursor` / `cursor-claude` | `claude-cursor` | `claude-cursor` |
| `gpt-5.5` / `gpt-5` / `openai` | `frontier-mcp` | `openai/gpt-5.5` |
| `gpt-5.4` | `frontier-mcp` | `openai/gpt-5.4` |
| `gemini` | `frontier-mcp` | `google/gemini-3-pro-preview` |
| `openai/gpt-5.5` (or any `provider/model`) | `frontier-mcp` | as supplied |
| `frontier-mcp [model]` | `frontier-mcp` | optional model lookahead |
| `--grok` | grok-build | grok-build |
| `--grok --ab` | grok-build + claude-web | A/B |
| `web-claude` | claude-web | claude-web |
| `claude-cursor` / `cursor-claude` | claude-cursor | claude-cursor |

`path` and `since` are **optional overrides** — the primary scope comes from
the session, not git:

| Argument | Meaning |
|---|---|
| omitted | Derive scope from the current session (primary) |
| `path` (existing repo path) | Narrow code review to that path if the session touched many things; session critique is always full-session |
| `since <ref>` | Optional git ref to confirm scope; accepts `penultimate commit` / `the penultimate commit` / `second-to-last commit` → `HEAD~1` |

Examples:

```
/session-review                                    # web-claude (default)
/session-review web-claude                         # web-claude (explicit)
/session-review claude-web                         # team_dispatch handoff → claude-web
/session-review claude-cursor                      # team_dispatch handoff → claude-cursor
/session-review gpt-5.5                            # frontier-mcp + openai/gpt-5.5
/session-review openai/gpt-5.4                     # frontier-mcp + openai/gpt-5.4
/session-review gemini                             # frontier-mcp + gemini
/session-review --grok                             # grok-build, claude-web fallback
/session-review --grok --tier thorough
/session-review --grok --ab
/session-review libs/agent_seat/registry.py
/session-review gpt-5.5 since HEAD~1
/session-review --grok since HEAD~1
/session-review services/rag/ since the penultimate commit
```

## Instructions

### 0. Resolve Dispatcher, Model, Path, Since Ref, and AB Mode

Parse args after `/session-review` using the **same model-resolution contract
as `/diff-review` step 0** (`MODEL_FAMILIES`, `GPT_VERSION_ALIASES`,
path-before-`/` disambiguation). Differences from diff-review:

- Default `DISPATCHER = "claude-web"`, `REVIEW_MODEL = "claude-web"`.
- `DISPATCHER` ∈ {`claude-web`, `claude-cursor`, `frontier-mcp`, `grok-build`}.
- `--tier` only applies when `DISPATCHER == "grok-build"`.

```
# Inherit from /diff-review step 0:
MODEL_FAMILIES, GPT_VERSION_ALIASES, repo_path_exists(), since aliases

DISPATCHER = "claude-web"
REVIEW_MODEL = "claude-web"
REVIEW_MODE = "web-claude"
BOOT = None
AB_MODE = False
TIER = "balanced"
PATH_ARG = None
SINCE_ARG = None

tokens = all args after /session-review
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

    if tok == "--grok" or tok == "grok":
        DISPATCHER = "grok-build"
        REVIEW_MODE = "grok-build"
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        if next_tok == "--ab":
            AB_MODE = True
            i += 2
        else:
            i += 1
        continue

    if tok == "--ab":
        AB_MODE = True
        i += 1
        continue

    if tok == "--tier":
        TIER = tokens[i + 1]  # quick|balanced|thorough|max
        i += 2
        continue

    if tok in {"web-claude", "claude-web"}:
        DISPATCHER = "claude-web"
        REVIEW_MODE = "web-claude"
        REVIEW_MODEL = "claude-web"
        i += 1
        continue

    if tok in {"claude-cursor", "cursor-claude", "cursor-lead"}:
        DISPATCHER = "claude-cursor"
        REVIEW_MODE = "cursor-claude"
        REVIEW_MODEL = "claude-cursor"
        i += 1
        continue

    if tok == "frontier-mcp":
        DISPATCHER = "frontier-mcp"
        REVIEW_MODE = "frontier-mcp"
        BOOT = "mcp"
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        if next_tok in MODEL_FAMILIES:
            REVIEW_MODEL = MODEL_FAMILIES[next_tok]; i += 2
        elif next_tok in GPT_VERSION_ALIASES:
            REVIEW_MODEL = GPT_VERSION_ALIASES[next_tok]; i += 2
        elif next_tok and "/" in next_tok and not repo_path_exists(next_tok):
            REVIEW_MODEL = next_tok; i += 2
        else:
            i += 1
        continue

  # Model tokens → frontier-mcp (same as /diff-review)
    if tok in MODEL_FAMILIES:
        DISPATCHER = "frontier-mcp"; REVIEW_MODE = "frontier-mcp"; BOOT = "mcp"
        REVIEW_MODEL = MODEL_FAMILIES[tok]; i += 1; continue
    if tok in GPT_VERSION_ALIASES:
        DISPATCHER = "frontier-mcp"; REVIEW_MODE = "frontier-mcp"; BOOT = "mcp"
        REVIEW_MODEL = GPT_VERSION_ALIASES[tok]; i += 1; continue
    if "/" in tok and not repo_path_exists(tok):
        DISPATCHER = "frontier-mcp"; REVIEW_MODE = "frontier-mcp"; BOOT = "mcp"
        REVIEW_MODEL = tok; i += 1; continue

    ask for clarification

if SINCE_ARG in {"penultimate commit", "the penultimate commit", "second-to-last commit"}:
    SINCE_REF = "HEAD~1"
else:
    SINCE_REF = SINCE_ARG
```

`ESCALATE_TO_WEB` is resolved **after** primary dispatch in step 6:

```
ESCALATE_TO_WEB = (
    AB_MODE
    or (stdout_truncated and has_non_suggestion)   # grok-build only
    or (gap_files and DISPATCHER == "frontier-mcp")  # Critical/Warning refs outside manifest
)
```

Report: dispatcher, `REVIEW_MODEL`, `AB_MODE`, `TIER` (if grok), `PATH_ARG`,
`SINCE_REF` (if set).

### 1. Derive File List from Session

Read the current conversation to identify:
1. Which files were created or modified (explicit from tool calls or conversation)
2. Why each file was changed (the task, the bug fix, the decision that drove it)
3. What the overall session arc was (what problem was being solved)

Format as a session file inventory table:

```
## Session File Inventory

| File | Change type | Why |
|---|---|---|
| libs/agent_seat/tool_friction.py | modified | C1+C2 fix: parallel-turn-1 tracker and classifier generalization |
| libs/agent_seat/test_tool_friction.py | new | 14 test cases for C1+C2 per claude-web architectural review |
| .cursor/commands/session-review.md | modified | Added grok-build dispatcher and A/B test mode |
```

Then verify against git to catch anything missed:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHA=$(git rev-parse --short HEAD)
git diff --name-only HEAD~1 HEAD    # if changes are committed
# or:
git status --short                  # if changes are uncommitted
```

**Cross-check rules**:
- If git shows files not in the session inventory, include them only if the
  session actually touched them. Git is confirmation, not scope expansion.
- If the session touched files that don't appear in git (e.g. `.cursor/`
  command files that aren't tracked, or `tmp/` files), include them in the
  narrative but exclude from the code review scope if they aren't source files.
- If `PATH_ARG` was supplied in step 0, filter the session inventory to that
  path now. Session critique remains full-session.
- If `SINCE_REF` was supplied, run `git diff --name-only $SINCE_REF HEAD` as
  an additional confirmation pass (not a replacement for the session inventory).

**Auto-exclude patterns** (lock files, generated artifacts, source maps):

```
EXCLUDE_RE='(package-lock\.json|yarn\.lock|\.lock$|\.min\.(js|css)$|\.map$|go\.sum$)'
```

**Source files** (reviewed by default — test files INCLUDED):

```
SOURCE_RE='\.(py|js|ts|jsx|tsx|php|go|rs|java|rb|c|cpp|h|cs|swift|kt)$'
```

Partition files from the session inventory into:
- `$SOURCE_FILES` — source files to review
- `$NONSOURCE_FILES` — config, infra, docs, prompts (agent picks below)
- `$AUTO_EXCLUDED` — matched by EXCLUDE_RE
- `$DELETED` — files the session deleted (excluded from review)

Track an **excluded list** — every file not sent to the reviewer, with a
one-word reason (`generated`, `deletion`, `non-source`, `sloc-skip`,
`path-filter`). This becomes `{EXCLUDED_FILES}` in step 5.

**SLOC gate** — for every file in `$SOURCE_FILES`, check its current line count:

```bash
SLOC_VIOLATIONS=""
for f in $SOURCE_FILES; do
    [ -f "$f" ] || continue
    lines=$(wc -l < "$f")
    if git cat-file -e "HEAD:$f" 2>/dev/null; then
        [ "$lines" -gt 400 ] && \
            SLOC_VIOLATIONS+="  $f — $lines lines (existing, limit 400)\n"
    else
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
  1. Address SLOC violations first, then rerun /session-review
  2. Continue review as-is (violations will appear as Critical findings)
```

Set `file_count` = number of reviewable source files from the session inventory.

Report: session file inventory table, git cross-check results, `file_count`.
(`ESCALATE_TO_WEB` is evaluated after grok-build findings in step 6.)

**Stop if scope is empty**: "No files changed in this session."

**Stop if > 150 files**: warn the user and offer to narrow with a `path` arg.

### 2. Extract Workspace Invariants

Assemble a compact invariants block from loaded workspace rules (already in
context). Cross-check loaded rule files:

```bash
ls .cursor/rules/*.mdc 2>/dev/null
ls ../.cursor/rules/*.mdc 2>/dev/null
```

Format a `<invariants>` block (≤ 50 lines) covering:

1. **Transport / communication** — UDS-first, required helpers, forbidden direct construction
2. **Event system** — `@event_factory` mandate, `Event()` ban, signal format
   `^[a-z]+(\.[a-z]+){1,4}$` (no underscores/hyphens/digits)
3. **API namespace** — URL path ownership, forbidden cross-namespace endpoints
4. **Code scope** — every changed line traceable to the task; no unrequested refactors
5. **Quality gates** — SLOC limits, exception handling, default-value logging
6. **Workspace-critical patterns** — `_ws.mdc` invariants specific to this workspace

Format each invariant on one line:

```
[transport] ¬ httpx.AsyncClient(transport=...) directly — use make_async_client()
[events]    ¬ Event() construction — use @event_factory decorated function
[signals]   signal format: ^[a-z]+(\.[a-z]+){1,4}$ — no underscores, hyphens, digits
...
```

### 3. Build Review Manifest

Source the manifest from the session file inventory (step 1), not from a git
diff scan. The reviewer reads files at their current live state.

At your discretion, also include entries from `$NONSOURCE_FILES` if they are
< 100 lines and have meaningful changes (config that drives behavior, infra,
schema, prompts). Every non-source file you skip goes in the excluded list with
reason `non-source`.

Annotate each file header with **changed symbols** extracted from the diff
hunk context (where available):

```bash
COMPARE_REF="${SINCE_REF:-HEAD}"
REVIEW_MANIFEST=""
for f in $SOURCE_FILES $CHOSEN_NONSOURCE; do
    [ -f "$f" ] || continue
    lines=$(wc -l < "$f")
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
        header="=== $f ($lines lines) | new file ==="
    else
        header="=== $f ($lines lines) | changed: <module-level> ==="
    fi
    REVIEW_MANIFEST="${REVIEW_MANIFEST}"$'\n'"$header"
done
printf '%s\n' "$REVIEW_MANIFEST"
```

Do **not** build `REVIEW_CODE` — the reviewer reads live files directly (via
`fs` for claude-web; via absolute paths for grok-build).

### 3b. Manifest Expansion

Run when `DISPATCHER ∈ {"grok-build", "frontier-mcp"}` (expansion feeds the
packet and informs the ESCALATE_TO_WEB predicate). For the claude-web path,
skip this step — claude-web discovers context via its own MCP tool calls.

The Cursor agent (the dispatching agent — not a subagent) runs targeted grep
searches to find files likely relevant to the review but not in the session scope.

For each changed symbol in `REVIEW_MANIFEST`, search for:

**1. Downstream callers** — who calls the changed functions/classes:

```bash
# For each exported function or class name in "changed:" annotations:
grep -r "symbol_name" libs/ services/ --include="*.py" -l
# Exclude the source file itself from results
```

**2. Test files** — tests that exercise the changed code (catches tests NOT
in the session diff):

```bash
grep -r "import.*module_name\|from.*module_name" tests/ libs/ services/ \
    --include="test_*.py" -l
```

**3. Contract consumers** — if the session narrative mentions a data structure
or dict key by name (e.g. "exhaustion_summary", "distinct_turns",
"failed_tools"), grep for those strings across the codebase:

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

Outputs carried forward:
- `BRANCH`, `HEAD_SHA`
- `$SOURCE_FILES`, `$NONSOURCE_FILES`, `$AUTO_EXCLUDED`, `$DELETED`
- `INVARIANTS` block (compact, ≤ 50 lines)
- `REVIEW_MANIFEST` (file paths, line counts, changed-symbol headers, expansion annotations)
- `EXCLUDED_FILES` list
- `EXPANDED_FILES` list (files added by expansion, for ESCALATE_TO_WEB gap detection)

### 4. Synthesize Session Narrative

Before writing the packet, synthesize the session's problem-solution arc from
the current conversation. This is the critique substrate — it must be accurate
and honest, including missteps, not just a clean summary of what worked.

The session file inventory from step 1 directly informs this narrative: each
file's "why" flows into the Key Decisions and Solution sections. The inventory
and the narrative describe the same facts — the inventory is the per-file view;
the narrative is the arc view. They must be consistent.

Write the narrative in this structure:

```markdown
## Session Narrative

### Problem Statement
<What the user asked. Exact or close-paraphrase. What was broken, missing,
or desired at the start of the session.>

### Investigation
<Ordered account of what was checked, traced, or tested to understand the
problem. Include dead ends and incorrect initial hypotheses. Do not sanitize.>

### Key Decisions
<For each significant decision made: what was decided, the rationale given
or inferred, and what alternatives were available but not taken.>

### User Corrections / Redirections
<Any point where the user corrected the approach, overrode a direction, or
provided feedback that changed the path. Quote or close-paraphrase the
correction. These are the highest-value critique targets.>

### Solution
<What was ultimately implemented. Why this solution was chosen.
What it does not solve or defers.>

### Open Questions
<Anything that remains uncertain about whether this was the right approach,
right scope, or right level of enforcement.>
```

This narrative is **not** a summary of what went well. It is the raw material
for the reviewer to form a critique. Be accurate about where the session
stumbled, where the user had to intervene, and where the final solution may
have gaps.

### 5. Write Packet

```
mkdir -p tmp/reviews
```

**claude-web path:**

```
PACKET="tmp/reviews/${BRANCH//\//-}-session-review-packet.md"
```

The packet MUST include all six required blocks per
`architecture-handoff-protocol.mdc`, adapted for the dual-dimension review:

**`<scope>`** — branch, HEAD, selection mode, file count, ±lines, session topic
(one line: what the session was about).

**`<invariants>`** — the compact block from step 2.

**`<excluded>`** — the excluded file list from step 1a.

**`<task_guidance>`** — two explicit review dimensions (see shared task guidance
block below — identical for both dispatchers).

**`<session_narrative>`** — the narrative synthesized in step 4.

**`<review_manifest>`** — the `REVIEW_MANIFEST` from step 3, with live-read
instructions:

```
Read each listed file live before forming code-review findings:

  fs(sandbox="workspaces", op="read", path="universal-llm-gateway/<path>")

Do NOT rely on file contents inlined in this packet — none are present.
The manifest's HEAD ({HEAD_SHA}) is the canonical review boundary.
```

**`<mcp_capabilities>`** — use the shared template from
`handoff-dispatchers.mdc` § "web-claude" `<mcp_capabilities>` block verbatim,
plus this session-critique addition:

```
For session critique, also query:

  cortex(tool="search", arguments='{"query": "<problem domain terms>", "limit": 10}')

— to find prior observations, decisions, or todos that bear on whether the
approach was correct and consistent with established direction.
```

**`<output_format>`** — use the **claude-web finding shapes** below.

---

**grok-build path:**

```
PACKET="tmp/reviews/${BRANCH//\//-}-session-review-grok-packet.md"
```

The grok packet uses the non-MCP dispatcher format per
`architecture-handoff-protocol.mdc`. Write the file with two clearly delimited
sections — `system_context` and `prompt`:

```
## [SYSTEM_CONTEXT]

<invariants>     — fully inlined compact block from step 2 (≤50 lines,
                   source-tagged; no fs() fetch)
<task_guidance>  — the two-dimension review task (see shared block below)
<output_format>  — use the grok finding shapes below (no Evidence: field)

## [PROMPT]

<scope>              — branch, HEAD, selection mode, file count, ±lines, topic
<session_narrative>  — the narrative synthesized in step 4
<review_manifest>    — REVIEW_MANIFEST from step 3, with absolute-path read
                       instructions (see below)
```

**`<mcp_capabilities>`** — use the `mcp_allowed_read_only` template verbatim
(grokbuild subprocess MCP is live per `decision:grokbuild-subprocess-mcp-supported`;
grok can call `fs()` to resolve file gaps itself during dispatch).

**When `AB_MODE = True`** (i.e., `--ab` was supplied), write both packets now —
before dispatch — because claude-web will always run:
- The grok-build packet above (to `...-grok-packet.md`)
- Also write the claude-web packet (six-block format, to `...-session-review-packet.md`
  — the same path as the default claude-web run)

**When `AB_MODE = False`**, write only the grok-build packet now. The
claude-web packet is written in step 6 if `ESCALATE_TO_WEB` fires after
grok-build findings are inspected.

Both packets share the same task guidance block and session narrative. Only
the invariant format (inlined vs. fetched via `fs`) and manifest read
instructions (absolute path vs. `fs(sandbox="workspaces", ...)`) differ.

Manifest read instruction for grok-build:

```
Read each listed file directly at its absolute path before forming
code-review findings:

  /mnt/torus/projects/universal-llm-gateway/<path>

The manifest's HEAD ({HEAD_SHA}) is the canonical review boundary.
```

---

**Shared task guidance block** (read from skill, embed verbatim into packet):

```
fs(sandbox='workspaces', op='md_read', path='universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md', section='Code Review Dimension')
fs(sandbox='workspaces', op='md_read', path='universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md', section='Session Critique Dimension')
fs(sandbox='workspaces', op='md_read', path='universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md', section='Discipline')
```

Concatenate the three sections into the `<task_guidance>` block (claude-web / frontier-mcp) or `[SYSTEM_CONTEXT]` § task_guidance (grok-build). Order: Code Review Dimension, Session Critique Dimension, Discipline.

**Consolidated output_format** — defined upstream in
`architecture-handoff-protocol.mdc` § "Block 6: `<output_format>`" → "v1
structured shape (Aider SEARCH/REPLACE patch dispatch)". Same v1 schema for
both dispatchers (Code Finding + Session Finding); grok-build omits `Evidence:`
only when grokbuild-dispatch lacks MCP (`mcp_forbidden`), under
`mcp_allowed_read_only` Evidence is required, same as claude-web.

Embed the v1 schema reference in the packet's `<output_format>` block:

```
v1 structured shape per `architecture-handoff-protocol.mdc` § "Block 6:
`<output_format>`" → § "v1 structured shape". Code Finding + Session Finding
both required; paused operations per protocol § "Paused operations". Reviewer
MUST NOT emit `NewlineMode`, `FileSha256Before`, `ExpectedCount` (dispatcher
computes). Verify allowlist closed per protocol § "Verify Allowlist (closed)".
```



### 6. Dispatch to Reviewer

**manual-seat path** (`claude-web` default | `claude-cursor`):

Use when `DISPATCHER ∈ {claude-web, claude-cursor}`. Operator may say only the
seat name (`to claude-web` / `to claude-cursor`) — that implies
`team_dispatch(op="handoff", …)` (those seats are not `generate` targets).

Write the six-block packet in step 5 to `tmp/reviews/...-session-review-packet.md`.
Then hand off via Stargate (pointer turn ≤25 lines is server-built — ¬ `agent_bus`
`post` with inlined packet content):

```
PACKET_WS = f"universal-llm-gateway/{PACKET}"   # PACKET from step 5
HANDOFF_ROLE = "claude-web" if DISPATCHER == "claude-web" else "claude-cursor"
# Aliases also valid: role="lead" (web), role="cursor-lead" (cursor)

HANDOFF = team_dispatch(
    op="handoff",
    role=HANDOFF_ROLE,
    packet_path=PACKET_WS,
    subject=f"Session review handoff — {BRANCH} @ {HEAD_SHA}",
    caller_agent="claude-cursor",
    tags=[
        f"project:{REPO_NAME}",
        "type:review",
        "type:session-critique",
        f"agent:{HANDOFF_ROLE}",
        "mode:session-review",
    ],
)
THREAD_ID = HANDOFF["thread_id"]
TO_AGENT = HANDOFF["to_agent"]
POLL_HINT = HANDOFF["poll_hint"]
```

Close your turn with `HANDOFF["push_reminder"]`. Report: `THREAD_ID`, `TO_AGENT`,
`PACKET_WS`, summary artifact path
(`tmp/reviews/${BRANCH//\//-}-session-review-summary.md`).

**Operator follow-up:** `claude-web` → push agent-bus; `claude-cursor` → open the
thread in Cursor (Multitask / `/agent-bus`), Opus in model picker if needed.

**Poll for reply (canonical):** `agent_bus(tool="wait", arguments=POLL_HINT["arguments"])`
— re-call until `complete=true`. ¬ `pipeline(op="result")` (no `execution_id`).

---

**frontier-mcp path** (model-token / explicit `frontier-mcp`):

Write the six-block packet to `tmp/reviews/${BRANCH//\//-}-session-review-packet.md`
(same shape as claude-web in step 5 — `<scope>`, `<invariants>`,
`<excluded>`, `<task_guidance>`, `<session_narrative>`, `<review_manifest>`,
`<mcp_capabilities>`, `<output_format>`). Use the adapted `<mcp_capabilities>`
and `<output_format>` blocks from `/diff-review` § 4m (frontier-mcp variant;
`Evidence:` required; no `<need>` tags).

Dispatch per `handoff-dispatchers.mdc` § `frontier-mcp`:

```
EXEC = frontier_dispatch(
    messages=[{"role": "user", "content": <packet body — narrative + manifest>}],
    boot="mcp",
    agent=None,
    model=REVIEW_MODEL,
    system=<invariants + mcp_capabilities + output_format>,
    reasoning_effort="high",
    caller_agent="cursor",
    max_tool_turns=100,
)
EXEC_ID = EXEC["execution_id"]
result = pipeline(op="result", execution_id=EXEC_ID, wait_seconds=60)
# Re-poll up to 5 × 60s. Findings in result.content.
```

Evaluate `ESCALATE_TO_WEB` if Critical/Warning findings reference files outside
the expanded manifest (same gap predicate as `/diff-review` § 4m). If true,
also run the manual-seat handoff above with `HANDOFF_ROLE="claude-web"`.

Report: `EXEC_ID`, `REVIEW_MODEL`, packet path, artifact path
(`tmp/reviews/${BRANCH//\//-}-session-review-summary.md`), `ESCALATE_TO_WEB`.

Failure handling: per `/diff-review` § 4m — Stargate down → offer retry or
`web-claude`; tool-loop exhausted → re-dispatch with higher `max_tool_turns`.

---

**grok-build path:**

Read the `[SYSTEM_CONTEXT]` and `[PROMPT]` sections from the packet written
in step 5 and dispatch (V2 async contract):

```python
# Step 1: Dispatch → 202 envelope
dispatch = grokbuild(
    op="build",
    cwd="/mnt/torus/projects/universal-llm-gateway",
    mode="read_only",
    system_context=<contents of [SYSTEM_CONTEXT] section from packet>,
    prompt=<contents of [PROMPT] section from packet>,
)
DISPATCH_ID = dispatch["dispatch_id"]

# Step 2: Persist dispatch_id immediately (crash resilience — see grokbuild skill)
cortex(tool="assert", arguments=json.dumps({
    "entity_id": f"session-review:{BRANCH}-{HEAD_SHA}",
    "claim": f"grok_dispatch_id = {DISPATCH_ID}",
    "confidence": "confirmed",
    "evidence": f"persisted immediately after 202 for crash recovery (session {SESSION_ID})",
    "derivation_type": "agent_observation",
}))

# Step 3: Poll until terminal state (V2 contract — V1 op='build_events' removed)
# Tier governs inner reasoning effort, not transport shape — same poll loop for all tiers.
import time
while True:
    snap = grokbuild(op="build_status", dispatch_id=DISPATCH_ID)
    if snap["state"] in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(3)

# Step 4: Fetch canonical result (stdout/stderr/audit_fields live here exclusively)
RESULT = grokbuild(op="fetch_result", dispatch_id=DISPATCH_ID)
```

**Handle 404 on `build_status` or `fetch_result`** (worker restart — tracker is in-memory):
move directly to `fetch_result`; 404 there means sidecar also lost — surface and offer
claude-web fallback. The persisted `dispatch_id` assertion (step 2) is the recovery anchor.

Inspect the result:

1. `RESULT["audit_fields"]["read_only_violation"]` — if `True`, surface as a
   warning: grok performed unexpected writes in read-only mode.
2. `RESULT["metadata"].get("truncated")` — if `True`, note that `stdout`
   was truncated and offer to read the full sidecar at
   `RESULT["audit_fields"]["sidecar_path"]`.

The findings are in `RESULT["stdout"]`.

Report to the user: `dispatch_id`, `status`, packet path, artifact path
(`tmp/reviews/${BRANCH//\//-}-session-review-grok-summary.md`), and sidecar
path for full trace inspection.

**Evaluate `ESCALATE_TO_WEB`** now that findings are in hand:

```python
# grok can resolve file gaps itself via fs() under mcp_allowed_read_only;
# truncation is the only remaining escalation trigger beyond AB_MODE.
# Severity gate: don't pay the async claude-web round-trip when grok's
# findings are Suggestion-only — per Rules: "¬ escalate for Suggestion-only
# findings (AB_MODE bypasses severity check)". The check is heuristic on a
# truncated stdout (a Critical in the truncated tail could be missed), but
# Critical/Warning findings appear early enough to be visible pre-truncation
# in practice.
stdout_truncated = bool(RESULT["metadata"].get("truncated"))
has_non_suggestion = (
    "Severity:     Critical" in RESULT["stdout"]
    or "Severity:     Warning" in RESULT["stdout"]
)

ESCALATE_TO_WEB = (
    AB_MODE                                       # --ab flag: always escalate
    or (stdout_truncated and has_non_suggestion)  # truncation matters only when
                                                  # real findings are at stake
)
```

Report the predicate evaluation: `ESCALATE_TO_WEB = True/False` with the
triggering condition (AB_MODE / stdout truncated).

**When `ESCALATE_TO_WEB = True`**:

If `AB_MODE = False` (i.e., the claude-web packet was not written in step 5),
write it now using the six-block format to `...-session-review-packet.md`.

Then run the manual-seat handoff above with `HANDOFF_ROLE="claude-web"` (using the
claude-web packet). Capture `THREAD_ID`.

Report to the user: grok-build result is in hand (`dispatch_id`, `status`);
`ESCALATE_TO_WEB = True` — claude-web reply is pending asynchronously
(`THREAD_ID`). Proceed to triage grok-build findings immediately (step 7);
claude-web triage happens when the reply arrives.

**When `ESCALATE_TO_WEB = False`**: no claude-web dispatch. Proceed directly
to triage of grok-build findings (step 7, grok-build path).

### 7. Reply Handling

**manual-seat path** (`claude-web` | `claude-cursor`):

Manual async workflow. After handoff, stop until the operator completes the
`push_reminder` step and a reply lands. Poll with `POLL_HINT` (or inspect when
the user signals a reply has arrived).

#### 7a. Check for `<need>` tags

Parse and handle exactly as `/diff-review` step 4a. Max one `<need>` round per
pass. For session-critique `<need>` requests (e.g. asking for transcript or
prior session context): if the path exists in `workspaces`, fetch via `fs`; if
it is in `cortex`, reply with the relevant Cortex query for Claude Web to run.

#### 7b. Triage findings

Apply the validation contract from
`architecture-handoff-protocol.mdc` § "Validation Contract".

Triage code findings (five-bucket) and session findings (five-bucket)
separately. Session findings have a different rejection pattern — a session
finding cannot be "rejected by rule" the way a code finding can. For session
findings, the equivalent is "rejected by context": the finding assumes
something about the situation that is factually wrong given what actually
happened. State the correction.

**Partition by Operation before triage:**

```
APPLY_NOW   = findings with Operation ∈ {replace, create_file, delete_file,
                                          delete_substring, replace_whole_file,
                                          replace_all_occurrences}
PLAN        = findings with Operation = plan_required
NEEDS_INFO  = findings with Operation = needs_info
DEFERRED    = findings with Operation = deferred
BLOCKED     = findings with Operation = blocked
```

Per-finding granularity is mandatory: a paused finding in any of
`{PLAN, NEEDS_INFO, DEFERRED, BLOCKED}` does NOT block the rest. The
dispatcher applies every `APPLY_NOW` finding whose `DependsOn` is satisfied,
and surfaces the paused set separately in the triage summary.

**Dependency resolution** (executed before apply in step 8d):

1. Topologically sort `APPLY_NOW` by reviewer-declared `DependsOn`.
2. Compute mechanical dependencies: any two `APPLY_NOW` findings whose edits
   touch the same file get an implicit `DependsOn: F<earlier>[applied]`.
3. For each finding F: if any dependency F' is in `{PLAN, NEEDS_INFO,
   DEFERRED, BLOCKED}` or was rejected, mark F as `dependency_unmet`.
   `dependency_unmet` findings are surfaced separately — they are not
   applied this round.
4. Severity is preserved across all paused states. A Critical/blocked or
   Critical/dependency_unmet finding remains Critical in every summary.

**Anti-cheat validation** — reject any finding that:

- Declares `FileReadVia: not_read` AND `Operation ∈ APPLY_NOW`. Routing fix:
  request reviewer regenerate as `needs_info`.
- Declares `Operation: needs_info` with neither `WouldFetch:` nor `Questions:`.
- Declares `Operation: deferred` with fewer than 2 `Options:` entries.
- Declares `Operation: blocked` without both `BlockedReason:` (closed enum)
  AND `UnblockedBy:` (action-verb sentence).

Report: `{N} code + {M} session findings → APPLY_NOW {a}, PLAN {p},
NEEDS_INFO {n}, DEFERRED {d}, BLOCKED {b}, dependency_unmet {u} → after
apply: {applied} applied, {rejected} rejected, {surfaced} surfaced`.

#### 7c. Post pushback

Reply on the thread with triage outcome. For session findings, pushback may
include: "this correction was already given by the user before the agent
reached the conclusion you're critiquing" — be precise about the timeline.

#### 7d. Convergence check

Same convergence signals as `/diff-review` (step 4w, convergence signals list).
Stable disagreement on session critique findings is acceptable — record it.

---

**frontier-mcp path** (model-token / explicit `frontier-mcp`):

Findings are returned synchronously via `pipeline(op="result")` from step 6. Skip 7a (`<need>` is a web-claude affordance only) and 7c (no thread to push back on).

Proceed directly to 7b (triage) with the inline content from `result.content`. Apply the same five-bucket validation contract for both code and session findings.

Report the same triage summary: `{N} code + {M} session findings -> ...`.

If `ESCALATE_TO_WEB = True` was set after the gap-files predicate evaluation in step 6 (Critical/Warning findings reference files outside the expanded manifest), the claude-web reply is pending asynchronously. Triage frontier-mcp findings immediately; claude-web triage happens when its reply arrives, then enter the comparison pass per the Escalation path below.

If findings are insufficient or the reviewer reported tool-loop exhaustion (per `/diff-review` § 4m Failure handling), re-dispatch with higher `max_tool_turns` rather than silently accepting an incomplete result.

No convergence loop — frontier-mcp is single-pass per dispatch. If iteration is needed, re-run /session-review with adjusted scope.

---

**grok-build path:**

No async wait. Findings are in `RESULT["stdout"]` from step 6. Skip 7a
(`<need>` tags) and 7c (thread pushback) — grok-build is one-shot and has no
reply mechanism.

Proceed directly to triage (7b) with the inline findings. Apply the same
five-bucket validation contract for both code and session findings.

Report the same triage summary: `{N} code + {M} session findings → ...`.

If findings are insufficient or the response appears truncated, offer to
re-dispatch with additional corpus staged in the prompt — do not silently
accept an incomplete result.

No convergence loop — grok-build is single-pass. If a material disagreement
with a finding cannot be resolved inline, surface it for the user to decide
whether to escalate to claude-web for a second-opinion review.

After triage, apply findings per step 8d. If any finding references a file
not in the manifest, offer to re-dispatch with that file added rather than
silently skipping the finding.

---

**Escalation path (`ESCALATE_TO_WEB = True`, grok-build + claude-web):**

1. **Triage grok-build findings inline** (as in the grok-build path above) —
   produce the grok triage summary immediately.
2. **Enter async wait for claude-web** (as in the claude-web path: 7a `<need>`
   handling, 7b triage, 7c pushback, 7d convergence). In the escalation body,
   note which grok-build findings prompted the escalation (gap files or
   truncation).
3. **Comparison pass** — once both sets of findings are available:

```
## A/B Comparison

Agreements (both reviewers flagged):
  — high-confidence signal; apply directly

grok-build only:
  — list findings; assess whether absence from claude-web indicates
    overreach or a blind spot in claude-web's MCP-grounded approach

claude-web only:
  — list findings; assess whether absence from grok-build reflects
    lack of cortex/RAG context or a false positive from MCP evidence

Divergences are the primary A/B learning signal — surface them explicitly
with a brief note on what the disagreement reveals about each reviewer.
```

The comparison pass produces the content for the A/B artifact in step 8c.
Triage decisions (apply/reject/surface) from the comparison pass supersede
the per-reviewer triage done in sub-steps 1 and 2.

#### 7e. Merge and Stage Findings for Apply

Produce a merged finding list with source attribution from the comparison pass:

```
## Merged Findings — Ready to Apply

### Agreements (apply directly — both reviewers confirmed)
- [code/session] {finding summary}  (source: both)

### grok-build only (validate then apply)
- [code/session] {finding summary}  (source: grok)

### claude-web only (validate then apply)
- [code/session] {finding summary}  (source: claude-web)

### Deferred / Surfaced
- {finding} — {reason}
```

Triage rules for the merged set:
- **Agreements**: apply Critical immediately; present Warning/Suggestion for user confirmation
- **Single-source findings**: apply the same triage as if from one reviewer — Critical applies, Warning/Suggestion presented
- Source metadata (`source: grok | claude-web | both`) is recorded in the artifact but does NOT change the triage bucket — a finding is valid or it isn't regardless of source
- Proceed to step 8d for the structured apply workflow.

### 8. Close and Audit

#### 8a. Documentation contract audit

Same for both dispatchers. Check event-contracts.md and relevant docs for any
applied code findings that changed contracts (per `/diff-review` step 5a).

#### 8b. Close thread

**claude-web path:**

```python
agent_bus(tool="close", arguments={
    "thread": THREAD_ID,
    "summary": f"Session review {BRANCH}: {code_applied} code + {session_applied} session findings applied, {rejected} rejected, {surfaced} surfaced",
})
```

**grok-build path:** No `agent_bus` close needed — there is no thread.

**Escalation path (`ESCALATE_TO_WEB = True`):** Use the claude-web close call
above (a thread exists from the claude-web dispatch). Include the grok-build
`dispatch_id` and escalation reason in the summary:

```python
agent_bus(tool="close", arguments={
    "thread": THREAD_ID,
    "summary": f"Session review {BRANCH} (grok+web escalation): grok={grok_applied} applied, web={web_applied} applied, {agreements} agreements, {divergences} divergences",
})
```

#### 8c. Write artifact

**claude-web path** — write to `tmp/reviews/${BRANCH//\//-}-session-review-summary.md`:

```markdown
# Session Review: {BRANCH}

**Head**: {HEAD_SHA}  **Date**: {ISO-UTC}
**Reviewer**: Claude Web  **Thread**: {THREAD_ID}
**Scope**: {N} files, {±lines}  **Session topic**: {topic}
**Packet**: {PACKET}

## Code Findings

### Critical
{findings or "None"}

### Warnings
{findings or "None"}

### Suggestions
{findings or "None"}

## Session Critique Findings

### Critical
{findings or "None"}

### Warnings
{findings or "None"}

### Suggestions
{findings or "None"}

## Applied
- [{FindingID}] [code] {file} — {description}  (Edits: {N}, Verify: {pass/skip/fail})
- [{FindingID}] [session] {phase}: {description}
- (none)

## Rejected
- [{FindingID}] {finding} — overridden by rule/context: {reason}
- (none)

## Surfaced for Triage
- [{FindingID}] {finding} — {scope note}
- (none)

## Needs Info
- [{FindingID}] [code|session] {file or phase}: {Concern}
  - WouldFetch: {paths or queries}
  - Questions:  {operator questions}
- (none)

## Deferred for Discussion
- [{FindingID}] [code|session] {file or phase}: {Concern}
  - Options:        A) {approach} — {tradeoffs}
                    B) {approach} — {tradeoffs}
  - RecommendedNext: {e.g. "raise on agent-bus for second reviewer"}
- (none)

## Blocked (severity preserved)
- [{FindingID}] [{Severity}] [code] {file}: {Concern}
  - BlockedReason: {enum value}
  - UnblockedBy:   {action sentence}
  - Patch:         {present/absent — NOT applied this round}
- (none)

## Plan Required (severity preserved)
- [{FindingID}] [{Severity}] [code] {Scope}: {Concern}
  - AffectedFiles:    [...]
  - InvariantBroken:  {...}
  - WhyPatchIsUnsafe: {...}
  - MinimalPlan:      {one-line summary; full plan in finding body}
  - AcceptanceChecks: [...]
- (none)

## Dependency Unmet (paused awaiting upstream resolution)
- [{FindingID}] [{Severity}] {file or phase}: blocked by [{DependsOn FindingID}]
  - reason: dependency is in state {PLAN | NEEDS_INFO | DEFERRED | BLOCKED | rejected}
- (none)

## Documentation Contract Audit
- `docs/event-contracts.md`: updated | not needed: {reason}
- other docs: {paths} | not needed: {reason}

## Iteration History
- Turn 1 (cursor): initial handoff
- Turn 2 (claude-web): {summary} (sidecar: {URI if used})
- Turn 3 (cursor): {triage summary}
- ...
- Convergence signal: `{signal}` at turn {N}
```

**grok-build path** — write to `tmp/reviews/${BRANCH//\//-}-session-review-grok-summary.md`:

Use the same template as the claude-web path (Code Findings + Session
Critique Findings + Applied / Rejected / Surfaced / Needs Info / Deferred /
Blocked / Plan Required / Dependency Unmet + Documentation Contract Audit),
with the following header substitutions:

```markdown
# Session Review (grok-build): {BRANCH}

**Head**: {HEAD_SHA}  **Date**: {ISO-UTC}
**Reviewer**: grok-build  **dispatch_id**: {DISPATCH_ID}
**Scope**: {N} files, {±lines}  **Session topic**: {topic}
**Packet**: {PACKET}
**Sidecar**: {RESULT["audit_fields"]["sidecar_path"]}

... (same section layout as claude-web template) ...

## Dispatch Notes
- State: {snap["state"]}  (dispatch_id: {DISPATCH_ID})
- read_only_violation: {RESULT["audit_fields"]["read_only_violation"]}
- stdout truncated: {RESULT["metadata"].get("truncated", False)}
```

The grok-build template has no `Iteration History` section (single-shot).

**Escalation path (`ESCALATE_TO_WEB = True`)** — write individual artifacts
for both reviewers (using the claude-web and grok-build formats above), then
also write the combined comparison artifact to
`tmp/reviews/${BRANCH//\//-}-session-review-ab-summary.md`:

```markdown
# Session Review (grok+web): {BRANCH}

**Head**: {HEAD_SHA}  **Date**: {ISO-UTC}
**Scope**: {N} files, {±lines}  **Session topic**: {topic}
**grok-build dispatch_id**: {DISPATCH_ID}  **Sidecar**: {sidecar_path}
**claude-web Thread**: {THREAD_ID}
**Escalation trigger**: {AB_MODE / gap files: ... / truncated}
**Packets**: {GROK_PACKET} | {CW_PACKET}

## Comparison

### Agreement (both reviewers flagged)
{findings or "None"}

### grok-build only
{findings or "None"}

### claude-web only
{findings or "None"}

### Signal
<one paragraph: what the divergences reveal about each reviewer's strengths
or blind spots for this session type — e.g. grok-build catches structural
issues from raw file reads; claude-web catches context drift via cortex>

## Applied
- [{FindingID}] [code] {file} — {description}  (source: grok | claude-web | both)
- [{FindingID}] [session] {phase}: {description}  (source: grok | claude-web | both)
- (none)

## Rejected
- [{FindingID}] {finding} — overridden by rule/context: {reason}
- (none)

## Surfaced for Triage
- [{FindingID}] {finding} — {scope note}
- (none)

## Needs Info / Deferred / Blocked / Plan Required / Dependency Unmet
Use the same sub-sections as the single-reviewer templates above; carry
`source: grok | claude-web | both` per entry. A finding's pause state from
EITHER reviewer surfaces it here — agreements on the pause op are
strong signal; divergences (one reviewer says `blocked`, the other applies)
trigger comparison-pass review.

## Documentation Contract Audit
- `docs/event-contracts.md`: updated | not needed: {reason}
- other docs: {paths} | not needed: {reason}
```

#### 8d. Apply Findings — Ordered Dispatcher Contract

Run after the artifact is written (8c). Apply only the `APPLY_NOW` partition
from step 7b (findings with mechanical `Operation` and satisfied `DependsOn`).
Paused findings (`PLAN`, `NEEDS_INFO`, `DEFERRED`, `BLOCKED`, `dependency_unmet`)
are recorded in 8c and surfaced to the user — they are NEVER auto-applied.

The full apply contract lives upstream in the protocol — see
`architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply Contract" for:

- Per-finding ordered checks (PATH_EXISTS → PATH_BASE → SEARCH count →
  POST safety scan → StrReplace → Verify execution → severity-gated user gates)
- Verify Allowlist (closed) — pytest, ruff check, ruff format, ruff check
  --select=UP --fix, mypy, python -m, npm test, cargo check, wc -l, rg
- Quality gates after every Critical apply (ruff/compileall)
- Artifact update after apply completion

This command (session-review) inherits the contract verbatim; no
session-review-specific apply mechanics are layered on top.

## Rules

**All dispatchers:**
- ¬ proceed if scope is empty (both code and session narrative must be non-trivial)
- Omitted args → **`web-claude`**; model tokens (`gpt-5.5`, `openai/gpt-5.4`, any
  `provider/model`) resolve to **`frontier-mcp`** per the shared contract with
  `/diff-review`
- ¬ sanitize the session narrative — include missteps and corrections accurately
- ¬ proceed past 150 files without user confirmation
- ¬ proceed past SLOC violations without user confirmation
- ¬ apply any finding without showing it first
- ¬ skip invariant validation for code findings — the reviewer is rule-blind on `.mdc` rules
- ¬ "reject by rule" a session critique finding — use "reject by context" with a factual correction
- ¬ close without completing the documentation contract audit
- ¬ batch-apply Warning or Suggestion findings without per-finding user confirmation
- ¬ close without running quality gates (`ruff` + `compileall`) on all Critical-applied files
- Source attribution (`source: grok | claude-web | both`) is metadata only — triage is by severity and rule cross-check, not by which reviewer flagged it

**Patch / paused-finding discipline (both dispatchers):**
- Per protocol § "v1 Dispatcher Apply Contract" and § "v1 Dispatcher Partition" in `architecture-handoff-protocol.mdc`: severity preservation across paused states, per-finding granularity (paused never blocks APPLY_NOW), `FileReadVia: not_read` hard interlock, anti-cheat required subfields (`needs_info` → `WouldFetch:` OR `Questions:`; `deferred` → `Options:` ≥2; `blocked` → `BlockedReason:` + `UnblockedBy:`), reviewer MUST NOT emit `NewlineMode` / `FileSha256Before` / `ExpectedCount`, Verify allowlist (closed), POST safety scan, mechanical `DependsOn` computation, Stale-PRE handling. The session-review command inherits the contract verbatim; no overlay.

**frontier-mcp only:**
- Selected via model token or explicit `frontier-mcp`; synchronous one-shot via
  `frontier_dispatch` + pipeline poll
- `Evidence:` required on every finding; no `<need>` tags
- Check tool-loop exhaustion per `/diff-review` § 4m failure handling
- `ESCALATE_TO_WEB` when Critical/Warning findings reference manifest gap files

**claude-web only:**
- Default dispatcher when args omit dispatcher and model tokens
- ¬ close thread before convergence signal or explicit user direction
- Agent bus thread state is the source of truth for review status
- Max one `<need>` fetch round per pass
- **Handoff via `team_dispatch(op="handoff")`** — packet on disk; pointer turn only
  (Stargate-built, ≤25 lines). ¬ `agent_bus(tool="post")` with inlined packet
  content. Violating this causes silent truncation and the reviewer abandons the pass.

**grok-build only:**
- `<invariants>` MUST be fully inlined in `[SYSTEM_CONTEXT]` — ¬ reference external paths
- Check `audit_fields.read_only_violation` before reporting findings — unexpected writes invalidate the review boundary
- If `stdout` is truncated, surface it and offer sidecar inspection before triaging
- ¬ `agent_bus close` — there is no thread to close
- One-shot by design: if findings are inadequate, offer re-dispatch with an expanded prompt corpus rather than iterating silently

**grok-build + escalation (`ESCALATE_TO_WEB = True`):**
- `ESCALATE_TO_WEB` is evaluated AFTER grok-build findings are in hand — not before dispatch
- Triage grok-build findings first (synchronous); don't wait for claude-web before presenting them
- Comparison pass runs only after both sets of findings are in hand
- Divergences are the primary signal — record them verbatim in the comparison artifact
- Apply/reject/surface decisions from the comparison pass supersede per-reviewer triage
- ¬ close agent-bus thread until claude-web convergence signal is reached
- When escalating due to truncation: note the dispatch_id and truncation context in the thread body
- ¬ escalate for Suggestion-only findings — only stdout truncation triggers escalation (AB_MODE bypasses severity check)

## Cognitive Fallback Protocol (claude-web)

Bare `/session-review` dispatches to claude-web directly (no fallback). claude-web
is also the escalation path when grok-build or frontier-mcp primary dispatch needs
a second opinion:

- Trigger: grok-build stdout truncated (non–Suggestion-only findings)
- Trigger: frontier-mcp Critical/Warning findings reference manifest gap files
- Trigger: user adds `--ab` flag explicitly
- Do NOT escalate for file-gap findings — grok resolves gaps itself via `fs()`
  under `mcp_allowed_read_only`; escalating for gap-triggered findings is obsolete
- Do NOT escalate for Suggestion-only findings — those don't warrant async wait
- When escalating due to truncation: include the dispatch_id and `audit_fields.sidecar_path`
  in the thread body so claude-web can reference the partial sidecar if needed
