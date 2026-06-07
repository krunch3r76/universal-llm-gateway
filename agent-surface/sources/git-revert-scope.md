<!-- target:* -->
# Git Revert Scope

**Invariant**: ∀ git-revert operation: scope MUST be explicit. The live checkout
is shared — a broad revert silently wipes in-progress edits, staged hunks, and
other agents' unrelated diffs.

**Forbidden** (broad — wipes the shared tree):
`git checkout -- .` · `git restore .` · `git reset --hard` (any form, incl.
`HEAD`) · any glob/recursive form touching files beyond the agent's own diff.

**Permitted** (scoped):
`git checkout -- <file>` (one explicit path per call) · `git restore <file>` ·
`git checkout -- <dir>/` only when the agent exclusively owns that subtree.

**Single-owner exception**: in a dedicated single-owner worktree, broad revert is
permissible; NOT in a shared live checkout.

Before any scoped revert: list the exact authored files, issue one targeted call
each (no glob / `.`), then verify with `git status --short`. In an attended editor
session, prefer the editor's undo / revert UI over the git CLI entirely.
<!-- /target:* -->
