# /review-apply

Apply validated findings from a `/diff-review`, `/diff-review-loop`, or
`/session-review` artifact — or directly from an agent-bus thread — in a
separate session, after re-validating against current live state.

This is the **cross-session apply phase** of the architecture-handoff-protocol.
It handles both review types (`diff` and `session`) and accepts three input
forms:

- A workspace artifact path (`tmp/reviews/<branch>-<type>.md`)
- A Cortex URI (`cortex://notes/system/reviews/<file>.md`)
- An agent-bus **thread number** (integer) — resolves to the sidecar artifact
  or extracts findings from the thread body directly

The review artifact is the handoff object. Live source files and loaded
workspace rules remain the authority. The corpus is stale by construction for
this command — every finding is re-validated against live files before apply.

Shared protocol:
- `architecture-handoff-protocol.mdc` § "Validation Contract" — five-bucket
  triage gate for every finding
- `architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply Contract" — the
  per-finding ordered checks for `Edits:` block application

## When to Use

- A previous `/diff-review` (any dispatcher) produced a review artifact and
  the session ended before all findings were applied
- A previous `/session-review` produced findings that need cross-session apply
- A `web-claude` agent-bus thread has converged and you want to apply its
  findings in a new Cursor session
- You want the review artifact persisted to Cortex before applying changes

## Invocation

```
/review-apply <thread-id | artifact-path | cortex-uri> [critical-only | include-warnings]
```

Examples:

```
/review-apply 1086
/review-apply tmp/reviews/feat-x-diff-review.md
/review-apply tmp/reviews/feat-x-session-review-summary.md
/review-apply tmp/reviews/feat-x-diff-review-loop.md critical-only
/review-apply cortex://notes/system/reviews/feat-x-diff-review-2026-04-25.md
/review-apply tmp/reviews/feat-x-diff-review.md include-warnings
```

Arguments:

- `<ref>` — required. Thread ID (integer), workspace path, or Cortex URI.
- `critical-only` — default. Apply only validated Critical findings.
- `include-warnings` — propose Warning fixes after Criticals; ask the user
  before editing each Warning. Suggestions always require explicit approval.

## Instructions

### 0. Resolve Input

**If `<ref>` is an integer**: treat as agent-bus thread number. Fetch:

```
THREAD = agent_bus(tool="fetch", arguments={"thread": <ref>, "compact": false})
```

Scan the thread body and all reply turns for a workspace path or Cortex URI
matching `tmp/reviews/` or `cortex://notes/system/reviews/`. If found, use it
as `ARTIFACT_REF` and proceed to the workspace/cortex path below.

If no artifact path is referenced in the thread, extract findings directly from
the thread reply body (treat the body text as the artifact content). Note in the
report: `Source: agent-bus thread <N> (inline, no sidecar artifact)`.

**If `<ref>` starts with `cortex://`**: strip prefix, read via
`fs(sandbox="cortex", op="read", path=CORTEX_PATH)`.

**If `<ref>` is a workspace path**: verify it exists, read locally. If the path
is under `tmp/reviews/`, persist to Cortex before edits begin (review artifacts
in `tmp/` are ephemeral):

```
CORTEX_PATH = f"notes/system/reviews/{basename_without_md}-{ISO_DATE}.md"
fs(sandbox="cortex", op="write", path=CORTEX_PATH, content=artifact_content)
PERSISTED_REVIEW_URI = "cortex://" + CORTEX_PATH
```

If the artifact lives outside `tmp/reviews/`, persistence is optional — ask
before copying.

Report: source resolved (thread / path / URI), Cortex URI if persisted, apply mode.

### 1. Detect Review Type

Parse the artifact header to set `REVIEW_TYPE`:

- `# Diff Review:` or `# Session Review (grok-build):` or `# Session Review
  (grok+web):` → check for `Session Critique Findings` section
- `REVIEW_TYPE = "session"` if `## Session Critique Findings` is present
- `REVIEW_TYPE = "diff"` otherwise

### 2. Parse the Artifact

Extract finding sections (case-sensitive headings):

**Always:**
- `## Critical`, `## Warnings`, `## Suggestions`
- `## Applied`, `## Rejected by Rules`, `## Surfaced for Triage`
- `## Pending User Decision` (optional), `## Iteration History`

**v1 schema detection** — set `IS_V1 = True` if any of these markers are
present: section heading `## Needs Info`, `## Deferred for Discussion`,
`## Blocked`, `## Plan Required`, or `## Dependency Unmet`; or `FindingID:`
field; or `Operation:` field. Otherwise `IS_V1 = False`.

**If `IS_V1 = True`, also extract:**
- `## Needs Info` → `NEEDS_INFO_FINDINGS`
- `## Deferred for Discussion` → `DEFERRED_FINDINGS`
- `## Blocked` → `BLOCKED_FINDINGS`
- `## Plan Required` → `PLAN_REQUIRED_FINDINGS`
- `## Dependency Unmet` → `DEP_UNMET_FINDINGS`

```
PAUSED_FINDINGS = (NEEDS_INFO_FINDINGS ∪ DEFERRED_FINDINGS ∪
                   BLOCKED_FINDINGS ∪ PLAN_REQUIRED_FINDINGS ∪
                   DEP_UNMET_FINDINGS)
```

**If `REVIEW_TYPE == "session"`**, also extract:
- `## Code Findings` → `## Critical` / `## Warnings` / `## Suggestions` under
  the code sub-heading (tag each finding `[code]`)
- `## Session Critique Findings` → same sub-headings (tag each finding
  `[session]`)

Per-finding metadata from v1 artifacts:
- `FindingID:` — stable id (`F<n>`)
- `Operation:` — the operation type
- `Edits:` block — present when Operation ∈ mechanical set

Findings in `PAUSED_FINDINGS` are never application candidates. A Critical
paused finding retains its severity in the closure artifact but is never applied.

If the artifact has no parseable findings and does not say `No findings.`,
stop and ask the user whether to proceed manually.

### 3. Re-Establish Current Repo State

```bash
git status --short
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD
```

Report current branch and HEAD. If the artifact's branch/head differs from
current, warn and ask whether to proceed. Do not require exact HEAD match —
review artifacts are routinely consumed after follow-up edits.

### 4. Re-Validate Findings Before Editing

For every finding considered for application:

1. Identify the target file(s) from the finding body.
2. Re-read the live file from the workspace.
3. Confirm the offending snippet or behaviour **still exists** (liveness
   check — corpus is stale by construction for this command).
4. Cross-check against loaded workspace rules (transport, model ID, event
   signal, exception handling, API namespace, change scope, SLOC quality).
5. For `[session]` findings: no liveness check (no code target); classify
   directly into `needs_user_decision` — session critique findings are never
   auto-applied.
6. Classify into one of:
   - `apply` — valid, in-scope, still present, has complete `Edits:` block
   - `already_resolved` — finding no longer applies to live code
   - `rejected_by_rule` — contradicts a workspace rule (record rule name)
   - `rejected_by_context` — session critique finding whose premise is
     factually wrong given what actually happened (record the correction)
   - `surface_for_triage` — real but outside current application scope
   - `needs_user_decision` — Warning/Suggestion, `[session]` finding, or
     ambiguous fix requiring operator input

For v1 artifacts, findings in `PAUSED_FINDINGS` skip re-validation — carry
them forward directly to the closure artifact under their respective paused
sections. Do not re-validate or apply any finding that appears in
`PAUSED_FINDINGS`.

> The review artifact is not truth. It is a handoff. Live source + loaded
> workspace rules decide whether a finding can be applied.

### 5. Apply Critical Findings

For every Critical `[code]` finding classified `apply`:

#### v1 Edits block apply (structured SEARCH/REPLACE)

For v1 findings with an `Edits:` block, run the full v1 Dispatcher Apply
Contract from `architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply
Contract":

- PATH_EXISTS → PATH_BASE → SEARCH count → POST safety scan → StrReplace →
  Verify (against closed Verify allowlist) → severity-gated user gates

Apply the liveness check on top of the contract: the target snippet must still
exist in the live file before the contract runs.

#### v1 findings without an `Edits:` block

These are in `PAUSED_FINDINGS` (Operation ∈ paused set) and are never applied.
Carry forward to the appropriate paused section in the closure artifact.

#### Old-schema (`IS_V1 = False`) Critical findings

Show the intended edit briefly. Apply the minimal change needed to resolve the
finding. Do not refactor adjacent code. Record the file and one-line fix.
Batch edits when multiple findings touch the same file.

If a Critical finding requires broader design work, move it to
`needs_user_decision` and ask for a plan approval instead.

#### `[session]` Critical findings

Never apply. Move to `needs_user_decision` immediately and surface to the user
with the session critique text. These carry no `Edits:` block by design —
they are interpretive findings about decision quality, not code patches.

### 6. Handle Warnings and Suggestions

Default mode is `critical-only`:
- Do NOT edit Warnings or Suggestions; add to `PENDING_USER`.

If mode is `include-warnings`:
- For each Warning classified valid (`[code]` only), show the proposed edit
  and ask before applying.
- `[session]` Warnings go to `PENDING_USER` regardless of mode.
- Suggestions always require explicit approval in any mode.

For v1 artifacts, also exclude any finding that appears in `PAUSED_FINDINGS`.

### 7. Verify

After substantive edits:

1. Re-read changed files.
2. Run targeted lint diagnostics for edited files via `ReadLints`.
3. For Python files changed:

   ```bash
   python -m compileall -q <changed-python-files>
   ruff check <changed-python-files>
   ```

4. If the artifact requested a specific verification command, run it unless
   destructive or out of task scope.

Record results even when verification cannot run (note why).

### 8. Documentation Contract Audit

After substantive edits:

1. If any applied finding changes event signals, payloads, semantics, failure
   modes, coordination behaviour, API surfaces, or user-visible contracts,
   re-read the relevant docs.
2. Update `docs/event-contracts.md` for event vocabulary changes (manually
   maintained; not generated).
3. Update other relevant docs when behaviour changed, or record why no docs
   update was needed.

Record the audit result in the closure artifact.

### 9. Write Closure Artifact

```
CLOSURE_PATH = f"notes/system/reviews/{basename_without_md}-apply-{ISO_DATE}.md"
fs(sandbox="cortex", op="write", path=CLOSURE_PATH, content=closure_markdown)
```

Closure artifact template:

```markdown
# Review Apply: {review title or branch}

**Source**: {ARTIFACT_REF or "agent-bus thread <N>"}
**Persisted review**: {PERSISTED_REVIEW_URI or "n/a"}
**Review type**: {diff | session}
**Current branch**: {BRANCH}
**Current HEAD**: {HEAD_SHA}
**Apply mode**: {critical-only | include-warnings}
**Date**: {ISO-UTC}

## Applied Now
- [{FindingID}] [code] {file}:{line} — {one-line fix}
- (none)

## Already Resolved
- [{FindingID}] {finding summary}
- (none)

## Rejected by Rules
- [{FindingID}] [code] {finding summary} — `{rule-file}`: {reason}
- (none)

## Rejected by Context (session critique)
- [{FindingID}] [session] {phase}: {Concern} — correction: {factual correction}
- (none)

## Surfaced for Triage
- [{FindingID}] {finding summary} — touches `{out-of-scope path}`: {scope note}
- (none)

## Pending User Decision
- [{FindingID}] [code|session] {finding summary}
- (none)

## Needs Info Carried Forward
- [{FindingID}] {file}: {Concern}
  - WouldFetch: {paths or queries}
  - Questions:  {operator questions}
- (none)

## Deferred for Discussion
- [{FindingID}] {file}: {Concern}
  - Options: A) {approach} — {tradeoffs}  B) {approach} — {tradeoffs}
  - RecommendedNext: {action}
- (none)

## Blocked (severity preserved)
- [{FindingID}] [{Severity}] {file}: {Concern}
  - BlockedReason: {enum value}
  - UnblockedBy:   {action sentence}
- (none)

## Plan Required (severity preserved)
- [{FindingID}] [{Severity}] {Scope}: {Concern}
  - AffectedFiles:    [...]
  - MinimalPlan:      {one-line summary}
- (none)

## Dependency Unmet
- [{FindingID}] [{Severity}] {file}: blocked by [{DependsOn FindingID}]
  - reason: dependency in state {PLAN | NEEDS_INFO | DEFERRED | BLOCKED | rejected}
- (none)

## Verification
- ReadLints: {clean | issues}
- compileall: {ok | failed | n/a}
- ruff: {ok | issues | n/a}
- requested verification: {description and result, or "none"}
- docs contract audit: {updated `docs/event-contracts.md` | updated other docs: <paths> | not needed: <reason>}
```

Report the closure URI and a one-line summary (e.g. `5 Critical applied, 1
already resolved, 2 surfaced — closure at cortex://notes/system/reviews/...`).

## Rules

- ¬ apply any finding in `PAUSED_FINDINGS`; carry forward verbatim to closure
- ¬ apply `[session]` findings; route to `needs_user_decision`
- For v1 `Edits:` blocks: run the full v1 Dispatcher Apply Contract per
  `architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply Contract" —
  PATH_EXISTS → re-read → SEARCH count → POST safety scan → StrReplace →
  Verify → severity-gated user gates; liveness check is required on top
- ¬ apply Warnings in `critical-only` mode (default)
- ¬ apply Suggestions automatically in any mode
- Always re-read live files before applying — corpus is stale by design
- Always audit docs/contracts after applied changes
- `docs/event-contracts.md` is manual; update it for any event contract change
- "Rejected by context" (session findings with false premise) must record the
  factual correction inline — it is not the same as "rejected by rule"
- If thread resolution yields no artifact path and no parseable findings,
  stop and report what was found in the thread rather than proceeding on inference

## Model Selection (when re-dispatching for additional review passes)

If a liveness check reveals the artifact is substantially stale (many findings
no longer apply) and the user asks for a fresh review pass before applying:

| Goal | Recommended model |
|---|---|
| Maximum ready-to-apply patches (`Edits:` blocks) | `frontier-mcp + openai/gpt-5.5` |
| Deep architectural or session critique, fewer auto-patches | `frontier-mcp + anthropic/claude-opus-4-7` |
| Adversarial cross-check on prior findings | `/review-apply` followed by multi-model chain per `.cursor/skills/multi-model-review/SKILL.md` |

`reasoning_effort="high"` is required regardless of model — it is the primary
driver of `Edits:` block completeness. Lower effort produces significantly more
`deferred` pause ops in place of concrete patches.
