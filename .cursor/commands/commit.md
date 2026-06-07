Stage changes and write a commit message.

**Workspace extensions** (this repo): read `.cursor/rules/commit_ws.mdc` — journal cross-reference, `/doc-check` on changed files, post-commit journal back-references and optional RAG re-index. Cursor `@commit_ws.mdc` resolves to the same file; do not rely on repo-root globs for `commit_ws.mdc` (it lives under `.cursor/rules/`).

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

Follow `.cursor/rules/commit_ws.mdc` pre-commit steps (journal cross-referencing,
`/doc-check`, etc.). If that file is missing, skip this step.

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
