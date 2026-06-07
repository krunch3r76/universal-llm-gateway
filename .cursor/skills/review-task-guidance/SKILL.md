---
description: task_guidance blocks for /session-review and /diff-review packets — embed Code Review, Session Critique, and Discipline sections by reference.
---

# Review task_guidance

This skill is the single source of truth for the task_guidance block embedded in /session-review and /diff-review packets. Both commands MUST embed by section reference, not by inlining.

## How to embed

At packet-build time, read this file (fs sandbox=workspaces, op=md_read, path=universal-llm-gateway/.cursor/skills/review-task-guidance/SKILL.md, section='Code Review Dimension') and inline the section body verbatim into the packet's <task_guidance> block (claude-web / frontier-mcp) or [SYSTEM_CONTEXT] § task_guidance (grok-build).

/session-review packets: embed Code Review Dimension + Session Critique Dimension + Discipline.
/diff-review packets: embed Code Review Dimension + Discipline.

## Section: Code Review Dimension

Review the listed files for:
1. Invariant violations — quote the offending line(s), name the specific
   invariant, give the concrete corrected code.
2. Correctness — logic errors, type mismatches, missing error handling,
   silent failure paths.
3. Change scope drift — unrequested refactors, adjacent reformatting,
   renamed variables outside the task.
4. Event signals — any new signal= string with underscores, hyphens, or
   digits; any Event(...) construction not through a factory.
5. Quality gates — SLOC, exception handling, defaults policy.
6. Documentation contracts — event/API/runtime contract changes without
   corresponding manual docs updates.
7. Line-density reading: Read each file completely line-by-line before moving to the next — do not skim at function-boundary density. Every import block, every exception handler, every assignment must be examined.
8. Cross-file duplication: When you encounter a constant, regex, or class definition, check whether the same definition appears in other manifest files you have already read. Flag any hand-mirrored duplicates that create divergence risk.
9. Exception handling patterns: flag `except Exception:` where `except ImportError:` is the correct contract for import-time fallbacks; flag `OSError` handlers that reconnect/retry/continue without logging the triggering error before doing so.

**Scope of code review**: the entire current contents of every file in the
manifest, not only the regions the session changed. Architectural, structural,
and absence findings are in-scope — use `Operation: plan_required` for those.

## Section: Session Critique Dimension

Critically evaluate the problem-solution arc in the Session Narrative:
1. Problem diagnosis — was the root cause correctly identified? Was there
   a simpler or more accurate framing available?
2. Solution appropriateness — is this the right level of intervention for
   the stated problem? Too broad? Too narrow? Wrong layer?
3. Decision quality — for each Key Decision: was the reasoning sound? Were
   the alternatives actually considered or just dismissed?
4. User corrections — for each correction listed: why did the agent reach
   the wrong approach? Was it a reasoning failure, an information gap, or
   a misread of the problem?
5. Alternatives — what better solutions were available that weren't taken?
   Propose them with concrete rationale.
6. Durability — will this solution hold, or does it address the symptom
   while the root cause remains?

Start with whichever dimension has higher-severity findings. Return
findings from both.

## Section: Discipline

**Hard interlock — read-before-patch.** If `FileReadVia: not_read`,
`Operation` MUST be `needs_info` or `deferred`. You MAY NOT emit
`replace` / `create_file` / `delete_file` / `delete_substring` /
`replace_whole_file` / `replace_all_occurrences` for a file you have not
read in this dispatch. The dispatcher will reject any such finding.

**Patch completeness — default to `Edits:` blocks.** For every finding
where you have read the file and the fix is well-scoped, you MUST emit a
complete `Edits:` block using column-0 SEARCH/REPLACE fences. Partial
patches (placeholders like `# ... rest unchanged`), prose descriptions
without patch content, and `Operation: replace` with no `Edits:` block are
all invalid output — the dispatcher will reject them.

If you cannot produce a complete, safe patch, choose the appropriate pause
op and fill ALL its required subfields:

| Situation | Correct pause op | Required subfields |
|---|---|---|
| Need to read files not in the manifest | `needs_info` | `WouldFetch:` (list the paths) or `Questions:` |
| Fix is safe but requires a design decision between ≥2 valid approaches | `deferred` | `Options:` (≥2 with tradeoffs), `RecommendedNext:` |
| Fix is known but unsafe to apply right now (sequencing, open dependency) | `blocked` | `BlockedReason:` (enum), `UnblockedBy:` (action sentence) |
| Fix requires coordinated changes across many files or architectural work | `plan_required` | `AffectedFiles:`, `InvariantBroken:`, `WhyPatchIsUnsafe:`, `MinimalPlan:`, `AcceptanceChecks:` |

"I would suggest…" without a pause op and without an `Edits:` block is not
valid output. If you have a recommendation but no concrete patch, use
`plan_required` with `MinimalPlan:` summarising the approach.

**Anti-cheat clauses — pick the right pause op:**

- `needs_info` REQUIRES `WouldFetch:` or `Questions:`. If you can articulate
  neither, the correct op is `deferred` or `blocked`.
- `deferred` REQUIRES `Options:` with ≥2 distinct approaches. If only one
  option exists, the correct op is `blocked` or `plan_required`.
- `blocked` is reserved for cases where applying a *known* fix is *unsafe
  right now*. If you reach for `blocked` because composing the patch is
  *hard*, the correct op is `plan_required` (with `MinimalPlan:` filled).

**Severity is preserved.** A Critical/blocked finding remains Critical in
the artifact and triage summary. Pause operations do NOT downgrade severity.

**Non-suppression.** Patch shape MUST NOT dictate which findings you
surface — it dictates how applicable findings are expressed once you have
decided to surface them. If a finding does not fit the edit shape, emit it
with `Operation: plan_required` (and a `Sketch:`/`MinimalPlan:`) or
`Operation: blocked` — not as silence.
