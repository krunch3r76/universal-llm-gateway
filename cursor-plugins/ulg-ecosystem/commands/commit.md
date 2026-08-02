Stage changes and write a commit message.

**Workspace extensions** (this repo): read `.cursor/rules/commit_ws.mdc` — event-catalog sync (`gen-event-catalog`), journal cross-reference, `/doc-check` on changed files, post-commit journal back-references and optional RAG re-index. Cursor `@commit_ws.mdc` resolves to the same file; do not rely on repo-root globs for `commit_ws.mdc` (it lives under `.cursor/rules/`).

## Instructions

### 1. Inspect Current State

Run in parallel:
```bash
git status
git diff --cached --name-only
git diff --name-only
```

Collect: staged files, unstaged modifications, untracked files.

### 2. Workspace Integration

**Event catalog** (when `scripts/gen-event-catalog` exists): if changed files touch
event sources (`**/events.py` under `services/`, `libs/`, `systems/`) or
`docs/event-contracts.md`:
1. `scripts/gen-event-catalog sync`
2. Stage `docs/event-contracts.md` with this commit
3. `scripts/gen-event-catalog check` must pass before commit

The pre-commit hook (when installed) runs the same sync + re-stage + check as a
safety net.

**Other workspace steps**: follow `.cursor/rules/commit_ws.mdc` pre-commit steps
(journal cross-referencing, `/doc-check`, etc.). Skip if that file is missing.

### 3. Stage

```bash
git add -A
```

If only specific files should be staged (e.g. excluding unrelated untracked files),
stage them individually. Use judgment — do not ask unless the untracked files are
clearly unrelated to the current work.

### 4. Write the Commit Message

Follow the repository's existing commit style (run `git log --oneline -10` to check).

### 5. Commit

```bash
git commit -m "$(cat <<'EOF'
{message}
EOF
)"
```

### 6. Workspace Post-Commit

Follow `.cursor/rules/commit_ws.mdc` post-commit steps (journal `## Commits` lines,
RAG index curl, etc.). If that file is missing, skip this step.

### 7. Report

Show the commit hash and one-line summary.

## Rules

- ¬ push (never push without explicit user request)
- ¬ amend unless user explicitly asks
- Commit message style: match `git log` history (imperative mood, lowercase type prefix)
