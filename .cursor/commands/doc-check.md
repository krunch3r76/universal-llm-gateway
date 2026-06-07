Check project docs for staleness and coverage gaps against current changes.

**Workspace**: Load `@doc-check_ws.mdc` if exists for doc-to-directory mapping.

## What This Is

Ensures project documentation stays current with the codebase. Detects three problems:
1. **Staleness**: changed files referenced by existing docs (doc may be inaccurate)
2. **Coverage gaps**: new files/directories not referenced by any doc
3. **Broken references**: paths mentioned in docs that no longer exist on disk

Can be invoked standalone or as part of `/commit`.

## Instructions

### 1. Identify Changed Files

```bash
git diff --cached --name-only
git diff --name-only
git ls-files --others --exclude-standard
```

Collect all changed, staged, and new untracked files. Filter to architecture-relevant
paths (source directories, not config/tmp).

### 2. Coverage Table

If `@doc-check_ws.mdc` exists, use the workspace-specific doc-to-directory mapping.
Otherwise, scan the project's docs directory and infer coverage from doc content.

### 3. Staleness Check

For each doc that covers a changed area:
1. Read the doc
2. Check if changed files are referenced (by path or module/class/endpoint)
3. If the change **adds/removes/renames** a module, class, endpoint → flag as **STALE**
4. If the change is internal (logic fix, refactor) → flag as **OK**

### 4. Coverage Gap Check

For new files: check if the relevant doc references the new file's directory or purpose.
New directory under source paths almost always needs a doc update.

### 5. Broken Reference Check

Extract source paths referenced in docs. Verify each exists on disk.

### 6. Report

```
## Doc Check Results

### Stale
- {doc} — {file} changed ({reason})

### Coverage Gaps
- {file} — not referenced by any doc

### Broken References
- {doc} references {path} — file does not exist

### OK
- {doc} — changes are internal only
```

### 7. Act

Standalone: propose specific doc updates.
From `/commit`: apply updates and stage them.

## Rules

- Source code is always authoritative
- Don't update docs for internal logic changes
- Do update docs for: new modules, new endpoints, renamed classes, changed flows
- Don't create new doc files without user approval
