<!-- target:* -->
# Assistant

## Approach
- Docstring-first, plan non-trivial (`<PLAN>`), linear logic, meaningful names

## Workflow
1. Investigate: search, read source, identify patterns
2. Root cause: compare paths, document findings
3. Propose: minimal scope, explain rationale
4. Verify: lint, test compat

## Quality
- Files <300 SLOC, split by responsibility
- Comments: "why" only, never "what"
- No auto-tests unless requested — load `testing-discipline.mdc` when editing tests, migrations, or phase specs

## API
- Verify before use: `dir()`, `help()`, source
- No guessing, no fallbacks

## Principles
- Investigate → propose, root cause not symptoms — in investigation AND implementation
- Architectural solutions > locks/semaphores
- No backward compat shims (sole maintainer)
- Every changed line traces to the task

## Locations
| Purpose | Path |
|---------|------|
| Docs | `docs/` |
| Changelog | `changelog/` |
| Summaries | `/tmp/summaries/` — lead-seat ephemeral scratch only; ¬ cursor-sdk dispatch deliverables (durable output → `cortex://` or `workspaces://` shares per packet) |
| Proposed | `/tmp/proposed-docs/` |
| Prompts | `tmp/prompts/` |
<!-- /target:* -->
