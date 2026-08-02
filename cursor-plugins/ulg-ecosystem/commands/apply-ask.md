Implement the actionable improvements from the last /ask response. Backward compatibility (BC) is NOT required.

Also PRUNE LEGACY CODE created by the breaking changes:
- Identify functions/classes/modules/adapters that became unused (e.g., prior entry points, aliases, shims).
- Confirm they have no remaining references in the project (search all imports/call sites/routers/exports).
- If truly unreferenced, delete them; update exports/__all__/index files and route registries accordingly.
- If referenced only by deprecated/legacy code slated for removal, remove those call sites too.
- If any item appears externally referenced (HTTP route, CLI entry, public SDK surface), keep for now but mark as “staged removal” and note in changelog.

Perform (in order):
1) Apply structural edits, renames, deletions from the last /ask plan.
2) Remove compatibility shims and obsolete branches.
3) Make error/async behavior explicit and deterministic.
4) Update imports, call sites, and docstrings to match new signatures.
5) Run a project-wide reference check to verify deleted symbols are not used (e.g., ripgrep or IDE index equivalent); fix any stragglers.
6) Update HTTP route bindings/OpenAPI if surfaces changed.
7) Add/adjust tests ONLY if the /ask marked specific high-risk surfaces; otherwise skip.

When finished, output a concise **CHANGELOG** with sections:
- Breaking Changes (API/signature changes)
- Removed Dead Code (list symbols/files deleted)
- Updated Call Sites (key modules touched)
- HTTP Surface Updates (routes/status/schema changes)
- Docs/Tests Updated (only if applicable)
- Staged Removals (remaining items and the condition to delete later)
- TODOs Requiring Manual Decision
