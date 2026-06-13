<!-- target:* -->
# Git Posture & Truth Substrate

## Invariant — canonical state is the working tree + cortex, not git

The on-disk working tree is the source of truth for *what exists* and *what it
says*; cortex/RAG for *provenance* and *decisions*; the running process for
*what is live*. Git is a checkpoint/transport layer over the tree — **NOT** the
project index. This repo is gitignore-heavy by design: `tasks/` and most of
`docs/` are intentionally untracked (local, RAG-indexed). "Not git-tracked" says
nothing about whether a file is real, canonical, or done.

∀ question "does X exist / is X canonical / is this mine / is this done": answer
from the tree (read the file), cortex (read the entity), or a live probe —
**never** from `git ls-files` / `status` / `log`. Inferring existence or
canonicality from git state is a category error, and "no established git
workflow" is the default, not an omission to patch.

## Positive corollaries (subsumes commit-optionality + revert-scope)

- **Commit = optional bookkeeping, never a gate.** On-disk ⟺ committed in how
  done/handoffable work is; commit is load-bearing only for rebuild-persistence
  of a *git-tracked config file*. ¬ a liveness / completion / finality gate —
  never gate, wait, or hand back "to commit". Liveness = loaded in the running
  process (verify via load-event + probe, not the tree).
- **Probe by reading, never by mutating.** Decide "mine vs pre-existing" from the
  file / traceback — never `git stash` / `checkout` / `reset` the shared tree.
- **Revert is scoped + explicit.** One owned path per call (`git checkout -- <file>`);
  never `checkout -- .` / `restore .` / `reset --hard` in the shared checkout.
  Attended editor → prefer the undo / revert UI over git CLI.

## Git CLI is warranted only when

operator asks to commit/branch/PR · a named workflow defines a commit/merge/
release step · staging a deliberate tracked-config change for rebuild-persistence.
Otherwise, don't reach for git.
<!-- /target:* -->
