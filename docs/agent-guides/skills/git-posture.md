---
description: On coding sessions — file existence, canonicality, authorship, done-ness, git CLI, cursor-sdk implement substrate, or before inferring liveness from git state. Load via entity agent_skill:git-posture or this path.
---

# Git Posture & Truth Substrate

## When to read

- Any question: does X exist / is X canonical / is this mine / is this done?
- Before git CLI or `git_*` MCP on the shared checkout
- Before inferring service failure from uncommitted or dirty git state
- Before cursor-sdk implement dispatch or git-integration-worker diagnostics
- When building handoffs, consults, reviews, or packets that touch repo state

Cross-seat: attended **Cursor IDE** sessions use IDE-native worktree/git posture for
local editing; the execution-lane rules below bind **cursor-sdk / gitworker
implement substrate** unless the operator directs otherwise.

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
canonicality from git state is a category error, and **no established git
workflow** is the default, not an omission to patch.

## Positive corollaries

- **Commit = optional bookkeeping, never a gate.** On-disk ⟺ done for handoff
  purposes; commit is load-bearing only for rebuild-persistence of a *git-tracked
  config file*. ¬ a liveness / completion / finality gate — never gate, wait, or
  hand back "to commit". Liveness = loaded in the running process (verify via
  load-event + probe, not the tree).
- **Probe by reading, never by mutating.** Decide "mine vs pre-existing" from the
  file / traceback — never `git stash` / `checkout` / `reset` the shared tree.
- **Revert is scoped + explicit.** One owned path per call (`git checkout -- <file>`);
  never `checkout -- .` / `restore .` / `reset --hard` in the shared checkout.
  Attended editor → prefer undo / revert UI over git CLI.

## Execution lanes

| Lane | Surface | Where work lands | Git protocol |
|---|---|---|---|
| **A — default implement** | `cursor-sdk` generate + `contract=implement` | Live master checkout (`GIT_INTEGRATION_SOURCE_REPO`, default `universal-llm-gateway`) | **No standing workflow.** Working tree = truth. Commits operator-initiated, sporadic. |
| **B — arc integrate** | `git_integrate` / `git_land` MCP (headless) | Arc worktree → master merge | Operator-gated approval fingerprints (`diff_sha256`, `paths_sha256`). See `agent_skill:lead-agent-git-integration`. |

Lane A is the default mechanical implement path. Lane B is **not** implied by a
cursor-sdk dispatch and **not** required before re-dispatch.

## Commit posture

Commits happen when the **operator** asks, or a **named workflow** explicitly
defines a commit/merge/release step. They are **sporadic** — absence of a commit
does not mean work is incomplete, undeployed, or unsafe to build on.

## What not to infer

- ¬ uncommitted code ⇒ broken deploy or dead HTTP listener
- ¬ must commit before re-dispatch
- ¬ git-tracked ⇒ canonical (`tasks/`, most `docs/` intentionally untracked)
- ¬ dirty `git status` ⇒ reload failed because of pending edits (read tree +
  logs + events instead)

## LLM context — no diffs

**Never submit git diffs, unified patches, or `git diff` output to LLMs** —
handoffs, consults, reviews, implement packets, or dispatch context.

Provide **whole files** (when bounded) or **relevant sections** via:

```
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/…")
fs(sandbox="workspaces", op="md_read", path="universal-llm-gateway/…", section="…")
```

`git_diff` exists for **operator approval binding** (`diff_sha256` → integrate/land
gates), not model context. Use `include_full_diff=false` when only fingerprints
are needed.

## Git CLI is warranted only when

operator asks to commit/branch/PR · a named workflow defines a commit/merge/
release step · staging a deliberate tracked-config change for rebuild-persistence.
Otherwise, don't reach for git.

## Related skills

- `lead-agent-git-integration` — arc `git_*` MCP tools (Lane B only)
- `architecture-invariants` — `[universal:git-posture]` tag one-liner in handoff Block 2
