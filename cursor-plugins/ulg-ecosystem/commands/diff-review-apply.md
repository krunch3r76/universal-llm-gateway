# /diff-review-apply

> **Deprecated** — superseded by `/review-apply`, which consolidates diff and
> session review apply into a single command and also accepts an agent-bus
> thread number as input. Use `/review-apply` for all new work.

Apply validated feedback from a `/diff-review` or `/diff-review-loop` review
artifact in a separate session, after re-validating against current live
state.

This is the **resume/apply phase** of the architecture-handoff-protocol when
the original session has ended. It exists especially for `team-generate` runs
that have no `agent_bus` thread to resume.

The shared rules govern what an artifact looks like, what validation means,
and what Apply/Reject/Triage mean:

- `architecture-handoff-protocol.mdc` § "Validation Contract" — the
  five-bucket triage that gates every finding before edit
- `architecture-handoff-protocol.mdc` § "Artifact" — the section structure
  this command parses

`/diff-review-apply` adds: artifact persistence to Cortex, re-validation
against current branch/HEAD, application loop with mode flag, closure
artifact written back to Cortex.

The review artifact is the handoff object. Live source files and loaded
workspace rules remain the authority.

## When to Use

- A previous `/diff-review` (any dispatcher) produced
  `tmp/reviews/<branch>-diff-review.md`
- A previous `/diff-review-loop` produced
  `tmp/reviews/<branch>-diff-review-loop.md`
- A new session needs to apply review feedback without relying on agent_bus
- You want the review artifact persisted to Cortex before applying changes

## Invocation

```
/diff-review-apply <artifact-uri-or-path> [critical-only | include-warnings]
```

Examples:

```
/diff-review-apply tmp/reviews/feat-x-diff-review.md
/diff-review-apply tmp/reviews/feat-x-diff-review-loop.md critical-only
/diff-review-apply cortex://notes/system/reviews/feat-x-diff-review-2026-04-25.md
/diff-review-apply tmp/reviews/feat-x-diff-review.md include-warnings
```

Arguments:

- `artifact-uri-or-path` — required. May be a workspace path
  (`tmp/reviews/<file>.md`), absolute path under the repo, or Cortex URI
  (`cortex://notes/system/reviews/<file>.md`).
- `critical-only` — default. Apply only validated Critical findings.
- `include-warnings` — propose Warning fixes after Criticals; ask the user
  before editing each Warning. Suggestions still require explicit approval.

## Instructions

### 0. Resolve Artifact

Parse first arg as `ARTIFACT_REF`. If omitted, stop and ask.

If `ARTIFACT_REF` starts with `cortex://`: strip prefix, read via
`fs(sandbox="cortex", op="read", path=CORTEX_PATH)`.

If `ARTIFACT_REF` is a workspace path: verify exists, read locally. If the
path is under `tmp/reviews/`, persist to Cortex first (review artifacts in
`tmp/` are ephemeral):

```
CORTEX_PATH = f"notes/system/reviews/{basename_without_md}-{ISO_DATE}.md"
fs(sandbox="cortex", op="write", path=CORTEX_PATH, content=artifact_content)
PERSISTED_REVIEW_URI = "cortex://" + CORTEX_PATH
```

If the artifact lives outside `tmp/reviews/`, persistence is optional — ask
before copying.

Report: artifact path/URI read, persisted Cortex URI (if created), apply
mode.

### 1. Parse the Artifact

Extract these sections (case-sensitive headings) per
`architecture-handoff-protocol.mdc` § "Artifact":

- `## Critical`, `## Warnings`, `## Suggestions`
- `## Applied`, `## Rejected by Rules`, `## Surfaced for Triage`
- `## Pending User Decision` (optional), `## Iteration History`

Build finding lists: `CRITICAL_FINDINGS`, `WARNING_FINDINGS`,
`SUGGESTION_FINDINGS`, `ALREADY_APPLIED`, `REJECTED_BY_RULE`,
`SURFACED_FOR_TRIAGE`, `PENDING_USER`.

If the artifact has no parseable findings and does not say `No findings.`,
stop and ask the user whether to proceed manually. Do not infer edits from
unstructured prose.

#### v1 Schema Detection (additive)

Detect v1 artifacts by any of these markers:
- Section heading `## Needs Info`, `## Deferred for Discussion`, `## Blocked`,
  `## Plan Required`, or `## Dependency Unmet` present in the artifact
- `FindingID:` field in a finding body
- `Operation:` field in a finding body

Set `IS_V1 = True` if any marker is found; `IS_V1 = False` otherwise.
Old artifacts (no v1 markers) pass through all steps unchanged — the legacy
path is not modified.

If `IS_V1 = True`, parse these paused-set sections in addition to the sections
above:

- `## Needs Info` → `NEEDS_INFO_FINDINGS`
- `## Deferred for Discussion` → `DEFERRED_FINDINGS`
- `## Blocked` → `BLOCKED_FINDINGS`
- `## Plan Required` → `PLAN_REQUIRED_FINDINGS`
- `## Dependency Unmet` → `DEP_UNMET_FINDINGS`

Build:

```
PAUSED_FINDINGS = (NEEDS_INFO_FINDINGS ∪ DEFERRED_FINDINGS ∪
                   BLOCKED_FINDINGS ∪ PLAN_REQUIRED_FINDINGS ∪
                   DEP_UNMET_FINDINGS)
```

For v1 `## Critical`, `## Warnings`, and `## Suggestions` sections, also
extract per-finding structured metadata when present:

- `FindingID:` — stable identifier (`F<n>`)
- `Operation:` — the operation type
- `Edits:` block — present only when Operation ∈ {replace, create_file,
  delete_file, delete_substring, replace_whole_file, replace_all_occurrences}

Findings in `PAUSED_FINDINGS` are never application candidates regardless of
severity. A Critical paused finding retains its severity label in the closure
artifact but is never applied.

### 2. Re-Establish Current Repo State

```bash
git status --short
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD
```

Report current branch and HEAD. If the artifact's branch/head differs from
current, warn and ask whether to proceed. Do not require exact HEAD match —
review artifacts are often consumed after follow-up edits.

### 3. Re-Validate Findings Before Editing

For every finding considered for application, run the protocol's validation
contract per `architecture-handoff-protocol.mdc` § "Validation Contract" plus
a **liveness check**:

1. Identify the target file(s) from the finding body.
2. Re-read the live file from the workspace.
3. Confirm the offending snippet or behavior **still exists** (the apply-time
   liveness check — corpus is stale by definition for this command).
4. Cross-check against loaded workspace rules (transport, model ID, event
   signal, exception handling, API namespace, change scope, SLOC quality).
5. Classify into one of:
   - `apply` — valid, in-scope, still present
   - `already_resolved` — finding no longer applies to live code
   - `rejected_by_rule` — contradicts a workspace rule (record rule name)
   - `surface_for_triage` — real but out of current application scope
   - `needs_user_decision` — Warning/Suggestion or ambiguous fix

For v1 artifacts (`IS_V1 = True`), findings in `PAUSED_FINDINGS` skip
re-validation entirely — carry them forward directly to the closure artifact
under their respective paused-set section. Do not re-validate or attempt to
apply any finding that appears in `PAUSED_FINDINGS`.

The protocol invariant applies:

> The review artifact is not truth. It is a handoff. Live source + loaded
> workspace rules decide whether a finding can be applied.

### 4. Apply Critical Findings

For every Critical finding classified `apply`:

1. Show the intended edit briefly.
2. Apply the minimal change needed to resolve the finding.
3. Do not refactor adjacent code (per `change-scope.mdc`).
4. Do not apply unrelated cleanup.
5. Record the file and one-line fix in `APPLIED_NOW`.

Batch file edits when multiple Critical findings touch the same file. Preserve
unrelated user changes in the working tree.

If a Critical finding requires broader design work, do not improvise. Move it
to `needs_user_decision` and ask for approval with a short plan.

#### v1 Edits Block Apply (structured SEARCH/REPLACE)

For v1 Critical findings (`IS_V1 = True`) with an `Edits:` block, apply via
the protocol's v1 Dispatcher Apply Contract — see
`architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply Contract" for the
per-finding ordered checks (PATH_EXISTS → PATH_BASE → SEARCH count →
POST safety scan → StrReplace → Verify), the closed Verify allowlist, and
severity-gated user gates.

The /diff-review-apply command adds an apply-phase liveness check on top of
the protocol's contract: every finding's target snippet must still exist in
the live file before the contract runs (this command's corpus is stale by
construction — § 3 above).

For v1 findings **without** an `Edits:` block (Operation ∈ paused set), they
are in `PAUSED_FINDINGS` and are never applied here.

For old-schema Critical findings (`IS_V1 = False`), the existing prose-based
apply path above applies unchanged.

### 5. Handle Warnings and Suggestions

Default mode is `critical-only`:
- Do NOT edit Warnings or Suggestions; add to `PENDING_USER`.

If mode is `include-warnings`:
- For each Warning classified valid, show the proposed edit and ask before
  applying.
- Suggestions still require explicit approval.

Never apply Suggestions automatically.

For v1 artifacts, also exclude any Warning or Suggestion finding that appears
in `PAUSED_FINDINGS` — paused findings are never presented for apply
regardless of mode or severity.

### 6. Preserve Rejected and Triaged Findings

Do not apply anything listed under `Rejected by Rules`.

For each `Surfaced for Triage` item:
- Do not edit unless the user explicitly asks.
- Keep it in the closure artifact with its scope note.

If a triaged item points to a real defect outside the reviewed file set,
mention it in the final response as follow-up work.

### 7. Verify

After substantive edits:

1. Re-read changed files.
2. Run targeted lint diagnostics for edited files via `ReadLints`.
3. If Python files changed, run focused checks when practical:

   ```bash
   python -m compileall -q <changed-python-files>
   ruff check <changed-python-files>
   ```

4. If the artifact requested a specific verification command, run it unless
   destructive or out of task scope.

If verification cannot run, record why in the closure artifact.

### 8. Documentation Contract Audit

After substantive edits, audit manual docs and contracts before writing the
closure artifact:

1. If any applied finding changes event signals, event payloads, event
   semantics, failure modes, coordination behavior, API surfaces, or other
   user-visible/runtime contracts, re-read the relevant docs.
2. Update `docs/event-contracts.md` for event vocabulary changes. The catalog
   table regions are generated from `@event_factory` call sites — regenerate
   via `gen-event-catalog`, never hand-edit inside `<!-- GENERATED -->`
   markers; the curated prose outside the markers is hand-authored.
3. Update other relevant docs when behavior changed, or record why no docs
   update was needed.
4. Do not edit generated metadata artifacts directly; follow the generated
   artifact rules if a finding points at managed RAG metadata.

Record the audit result in the closure artifact.

### 9. Write Closure Artifact

Persist application result to Cortex:

```
CLOSURE_PATH = f"notes/system/reviews/{basename_without_md}-apply-{ISO_DATE}.md"
fs(sandbox="cortex", op="write", path=CLOSURE_PATH, content=closure_markdown)
```

Closure artifact template:

```markdown
# Diff Review Apply: {review title or branch}

**Source review**: {ARTIFACT_REF}
**Persisted review**: {PERSISTED_REVIEW_URI or "n/a"}
**Current branch**: {BRANCH}
**Current HEAD**: {HEAD_SHA}
**Apply mode**: {critical-only | include-warnings}
**Date**: {ISO-UTC}

## Applied Now
- {file}:{line} — {one-line fix}
- (none)

## Already Resolved
- {finding summary}
- (none)

## Rejected by Rules
- {finding summary} — `{rule-file}`: {reason}
- (none)

## Surfaced for Triage
- {finding summary} — touches `{out-of-scope path}`: {scope note}
- (none)

## Pending User Decision
- {finding summary}
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

Report the closure artifact's Cortex URI and one-line apply summary to the
user (e.g. "5 Critical applied, 1 already resolved, 2 surfaced — closure at
`cortex://notes/system/reviews/...-apply-2026-04-25.md`").

## Diff-Review-Apply Specifics

This command is the apply-phase of the protocol; it adds:

- **Cortex persistence** — `tmp/reviews/` artifacts get copied to Cortex
  before edits begin so the source-of-truth survives session loss
- **Liveness check** — every finding is re-read against current live files
  (mandatory because corpus is stale by construction for this command)
- **Mode flag** — `critical-only` (default) vs `include-warnings`
- **Closure artifact in Cortex** — application outcome written back so the
  next session can find it
- **Documentation contract audit** — event/API/runtime contract changes must
  update manual docs such as `docs/event-contracts.md` or record why no doc
  update was needed

## Rules

- ¬ apply any finding in `PAUSED_FINDINGS` (Needs Info / Deferred for
  Discussion / Blocked / Plan Required / Dependency Unmet); carry forward to
  the paused-set sections of the closure artifact regardless of severity
- For v1 `Edits:` blocks: see `architecture-handoff-protocol.mdc` § "v1 Dispatcher Apply Contract" — PATH_EXISTS first, then re-read, then SEARCH count, then POST safety scan, then StrReplace, then Verify (against the closed Verify allowlist)
- ¬ apply anything from `Rejected by Rules` or `Surfaced for Triage` without
  explicit user direction
- ¬ apply Warnings in `critical-only` mode (default)
- ¬ apply Suggestions automatically in any mode
- Always re-read live files before applying — the artifact is stale by design
- Always audit docs/contracts after applied changes; in `docs/event-contracts.md`
  the catalog regions are generated (regenerate via `gen-event-catalog`, never
  hand-edit inside `<!-- GENERATED -->` markers), the surrounding prose curated
- All other rules inherited from `architecture-handoff-protocol.mdc` and
  `change-scope.mdc`
