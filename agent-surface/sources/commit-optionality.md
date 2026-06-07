<!-- target:* -->
# Commit Optionality

## Invariant — commit is optional; not a finality gate

∀ edit in the live checkout: it is real workspace state the moment it is on disk.
`git commit` is OPTIONAL bookkeeping — uncommitted-on-master ⟺ committed-on-master
in how real / durable / "done" the work is. Commit is load-bearing for exactly
one property:

- **Liveness** (loaded in the running process) — needs a deploy / service restart, not commit.
- **Provenance** (proof you did it) — needs a file read-back, not a SHA.
- **Rebuild-persistence** (survives a `--no-cache` rebuild) — needs committing a
  *git-tracked config file*. **Only here does commit matter, and it means
  "tracked," not "more final."**

Therefore: the completion claim for code work is "edited `<file>`, verified by
read-back / lint / test", never "committed". Ephemeral scratch (`tmp/`) is
ephemeral because it is `tmp/`, not because it is uncommitted. Commit freely as a
checkpoint, never *because* the work isn't "real" without it.

## "Is this mine vs pre-existing?"

Read the file / traceback to answer it. NEVER manipulate shared git state
(`git stash` / `git checkout` / `git reset`) to probe authorship — the live
checkout and global stash stack belong to no single agent and a broad operation
silently wipes other in-progress work.

| Bad | Good |
|---|---|
| `git stash` / `git checkout -- <file>` to check or revert an edit | read the traceback; use the editor's undo / revert |
| Mid-session `commit` / `land` to make work "count" | the edit already counts; commit only when asked |
<!-- /target:* -->
