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

**Default substrate:** attended **Cursor IDE** editing and **cursor-sdk** implement
both land on the **live shared checkout** (default `universal-llm-gateway`). The
execution-lane rules below bind that path unless the operator directs otherwise.

**¬ worktrees in the default path.** Cursor-sdk does **not** create git worktrees
today (planned future). Arc worktrees are optional — web-claude / API (grokbuild)
may use them; Lane B (`git_integrate` / `git_land`) merges an arc worktree when
the operator explicitly runs integrate/land. Neither is implied by a cursor-sdk
dispatch.

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
| **A — default implement** | `cursor-sdk` + attended Cursor IDE | Live shared checkout (`GIT_INTEGRATION_SOURCE_REPO`, default `universal-llm-gateway`) | **No standing workflow.** On-disk tree = truth. **No git worktree.** Commits sporadic (operator or agent discretion) — **`git diff` unreliable** (see below). |
| **B — arc integrate** | `git_integrate` / `git_land` MCP (headless) | Optional arc worktree → master merge | Operator-gated approval fingerprints (`diff_sha256`, `paths_sha256`). See `agent_skill:lead-agent-git-integration`. |
| **C — optional arc dev** | web-claude, API / grokbuild (operator choice); future cursor-sdk | Arc worktree under `ulg-arc-worktrees` | **Not** cursor-sdk today. Separate from Lane A; **`git diff` vs merge-base reliable**; may feed Lane B when integrate/land is requested. |

Lane A is the default mechanical implement path. Lanes B and C are **not** implied
by a cursor-sdk dispatch and **not** required before re-dispatch.

## Commit posture

Commits happen when the **operator** asks, when an **agent** chooses to commit
(sporadic, uncoordinated with task boundaries), or when a **named workflow**
explicitly defines a commit/merge/release step. Absence of a commit does not mean
work is incomplete, undeployed, or unsafe to build on — and sporadic commits on
master are why `git diff` is an unreliable change summary (see **Git diff
reliability**).

## What not to infer

- ¬ uncommitted code ⇒ broken deploy or dead HTTP listener
- ¬ must commit before re-dispatch
- ¬ git-tracked ⇒ canonical (`tasks/`, most `docs/` intentionally untracked)
- ¬ dirty `git status` ⇒ reload failed because of pending edits (read tree +
  logs + events instead)
- ¬ `git diff` on master ⇒ accurate scope of a task or session (sporadic commits
  break the baseline; read files + cortex instead)

## Git diff reliability

| Substrate | `git diff` reliable? | Why |
|---|---|---|
| **Lane A — live master checkout** | **No** | Commits are sporadic — operator-initiated or at agent discretion — so there is no stable arc boundary. Uncommitted edits, recent commits, and older `HEAD` mix in ways `git diff` vs `HEAD` cannot disambiguate. |
| **Arc worktree (Lanes B/C; future cursor-sdk)** | **Yes** | Diff vs merge-base is well-defined; Lane B `diff_sha256` / `paths_sha256` gates depend on this. Cursor-sdk will adopt worktrees in the future — until then, treat cursor-sdk as Lane A. |

On the default substrate, answer "what changed?" by **reading files** and **cortex
provenance** — not `git diff`.

## LLM context — no diffs

**Never submit git diffs, unified patches, or `git diff` output to LLMs** —
handoffs, consults, reviews, implement packets, or dispatch context. On Lane A
master this is especially wrong: diffs are unreliable even as a rough summary
(see above).

Provide **whole files** (when bounded) or **relevant sections** via:

```
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/…")
fs(sandbox="workspaces", op="md_read", path="universal-llm-gateway/…", section="…")
```

`git_diff` MCP exists for **operator approval binding** on arc worktrees
(`diff_sha256` → integrate/land gates), not model context and not for
reconstructing change scope on master. Use `include_full_diff=false` when only
fingerprints are needed.

## Git CLI is warranted only when

operator asks to commit/branch/PR · a named workflow defines a commit/merge/
release step · staging a deliberate tracked-config change for rebuild-persistence.
Otherwise, don't reach for git.

## Related skills

- `lead-agent-git-integration` — arc `git_*` MCP tools (Lane B only)
- `architecture-invariants` — `[universal:git-posture]` tag one-liner in handoff Block 2
