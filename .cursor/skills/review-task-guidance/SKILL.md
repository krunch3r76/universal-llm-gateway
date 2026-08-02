---
name: review-task-guidance
description: task_guidance blocks for /session-review and /diff-review packets — embed Code Review, Session Critique, and Discipline sections by reference.
trigger_match_terms: ["review-task-guidance", "review_task_guidance", "session-review", "diff-review", "task_guidance", "review-reasoning", "review packet", "code review", "session critique"]
related_skills: ["multi-model-review"]
---

# Review task_guidance

This file is the SOT for `<task_guidance>` embedded in `/session-review` and `/diff-review` packets. Packet builders MUST embed by section reference, not by maintaining inline copies.

## Embed contract

At packet build time, read this SOT section body verbatim into the packet guidance surface:

- web-anthropic / frontier-mcp: `<task_guidance>`
- grok-build: `[SYSTEM_CONTEXT] § task_guidance`

`/session-review` ⇒ embed `Code Review Dimension` ∪ `Session Critique Dimension` ∪ `Discipline`.  
`/diff-review` ⇒ embed `Code Review Dimension` ∪ `Discipline`.

## Diff-prohibition (model context)

Unified-diff / patch bodies must not enter model-facing review context.

- NEVER embed unified-diff hunks, `+/-` patch lines, or a `git_diff` full body in review packets.
- Host discovery MAY use `git status`, `git diff --name-only`, and local `git diff` on the dispatcher host for scope and changed-symbol extraction only — packet output is file paths, changed-symbol names, and line counts.
- Reviewers read complete current file contents via `fs` (whole-file / `md_read` section review).
- Compact `git_diff` metadata (`diff_sha256`, `diffstat`, branch, `includes_uncommitted`) is permitted; models must NOT pass `include_full_diff=true`.

## Code Review Dimension

Review the complete current contents of every manifest file, not only changed regions. Read line-by-line before moving files; imports, handlers, assignments, and absence/structure are in scope.

Findings to surface:

1. Invariant violations — quote offending line(s), name invariant, give corrected code.
2. Correctness — logic/type errors, missing error handling, silent failure paths.
3. Scope drift — unrequested refactors, adjacent reformatting, unrelated renames.
4. Event signals — new `signal=` strings with underscores/hyphens/digits; `Event(...)` not through a factory.
5. Quality gates — SLOC, exception handling, defaults policy.
6. Documentation contracts — event/API/runtime contract changes without manual-doc updates.
7. Cross-file duplication — constants/regex/classes mirrored across manifest files with divergence risk.
8. Exception patterns — `except Exception:` where `ImportError` is the import-fallback contract; `OSError` reconnect/retry/continue without logging the triggering error.

Architectural, structural, and absence findings are valid. Use `Operation: plan_required` when a direct patch is unsafe.

## Session Critique Dimension

Critique the Session Narrative’s problem→solution arc:

1. Problem diagnosis — correct root cause? simpler/accurate framing missed?
2. Solution fit — right intervention layer and scope?
3. Decision quality — alternatives considered or prematurely dismissed?
4. User corrections — reasoning failure, information gap, or misread?
5. Alternatives — better available solutions with concrete rationale.
6. Durability — symptom fix or root-cause fix?

Start with the dimension containing higher-severity findings. Return findings from both dimensions when both apply.

## Discipline

### Read-before-patch interlock

`FileReadVia = not_read ⇒ Operation ∈ {needs_info, deferred}`. Do not emit `replace`, `create_file`, `delete_file`, `delete_substring`, `replace_whole_file`, or `replace_all_occurrences` for unread files.

### Patch completeness

For every finding where the file was read and the fix is well-scoped, emit a complete `Edits:` block with column-0 SEARCH/REPLACE fences.

Invalid output:
- placeholders such as `# ... rest unchanged`;
- prose-only fixes when a safe patch is available;
- `Operation: replace` without `Edits:`.

### Pause operations

If no complete safe patch can be emitted, choose the precise pause op and fill required subfields:

| Situation | Operation | Required subfields |
|---|---|---|
| Need files outside manifest | `needs_info` | `WouldFetch:` paths or `Questions:` |
| Safe fix needs design choice among ≥2 valid approaches | `deferred` | `Options:` ≥2 with tradeoffs; `RecommendedNext:` |
| Known fix unsafe now due sequencing/dependency | `blocked` | `BlockedReason:` enum; `UnblockedBy:` action sentence |
| Coordinated/architectural multi-file work | `plan_required` | `AffectedFiles:`, `InvariantBroken:`, `WhyPatchIsUnsafe:`, `MinimalPlan:`, `AcceptanceChecks:` |

Anti-cheat:
- `needs_info` requires `WouldFetch:` or `Questions:`.
- `deferred` requires ≥2 distinct `Options:`.
- `blocked` is for unsafe-now known fixes, not hard patch composition; hard/coordinated patching ⇒ `plan_required`.

### Non-suppression and severity

Patch shape controls expression, not whether to surface a finding. If a finding does not fit edit shape, emit `plan_required` or `blocked`; never silence it. Pause operations do not downgrade severity.
