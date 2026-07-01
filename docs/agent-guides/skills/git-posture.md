---
description: On coding sessions — file existence, canonicality, authorship, done-ness, git CLI, cursor-sdk implement substrate, or before inferring liveness from git state. Load via entity agent_skill:git-posture or this path.
---

# Git Posture & Truth Substrate

## When to read

Read on:
- "does X exist / is X canonical / is this mine / is this done?"
- before git CLI or `git_*` MCP on the shared checkout;
- before inferring service failure from uncommitted or dirty git state;
- before cursor-sdk implement dispatch or git-integration-worker diagnostics;
- when handoffs, consults, reviews, or packets touch repo state.

Default substrate: attended Cursor IDE editing and cursor-sdk implement both land on the live shared checkout (default `universal-llm-gateway`). These lane rules bind unless the operator directs otherwise.

`default_path ⇒ ¬worktree`. Cursor-sdk does not create git worktrees today. Arc worktrees are optional for web-claude/API/grokbuild or Lane B integrate/land; they are not implied by cursor-sdk dispatch.

## Invariant — truth substrate is working tree + Cortex + live process, not git

- What exists / what file says: on-disk working tree.
- Provenance / decisions: Cortex/RAG.
- What is live: running process, verified by load-event + probe.
- Git: checkpoint/transport layer, not project index.

This repo is gitignore-heavy by design: `tasks/` and most `docs/` are intentionally untracked. `not_git_tracked ⇏ unreal ∨ noncanonical ∨ undone`.

`question ∈ {exists, canonical, mine, done} ⇒ answer_from(tree_read ∨ cortex_read ∨ live_probe) ∧ ¬answer_from(git ls-files/status/log)`.

Inferring existence/canonicality from git state is a category error. No established git workflow is the default; this is not an omission to patch.

## Positive corollaries

- Commit = optional bookkeeping, never a gate. On-disk is done for handoff; commit is load-bearing only for rebuild-persistence of a git-tracked config/source file. `¬gate ∧ ¬wait ∧ ¬handoff_to_commit`.
- Liveness = loaded in running process. Verify with load-event + probe, not tree or commit.
- Probe by reading, never mutating. Decide "mine vs pre-existing" from file/traceback, not `git stash` / `checkout` / `reset`.
- Revert is scoped + explicit. One owned path per call; never `checkout -- .`, `restore .`, or `reset --hard` in shared checkout. Attended editor ⇒ prefer undo/revert UI.

## Execution lanes

| Lane | Surface | Where work lands | Git protocol |
|---|---|---|---|
| A — default implement | cursor-sdk + attended Cursor IDE | Live shared checkout (`GIT_INTEGRATION_SOURCE_REPO`, default `universal-llm-gateway`) | No standing workflow. On-disk tree = truth. No git worktree. Commits sporadic; `git diff` unreliable. |
| B — arc integrate | `git_integrate` / `git_land` MCP | Optional arc worktree → master merge | Operator-gated approval fingerprints (`diff_sha256`, `paths_sha256`); see `agent_skill:lead-agent-git-integration`. |
| C — optional arc dev | web-claude, API/grokbuild, future cursor-sdk | Arc worktree under `ulg-arc-worktrees` | Not cursor-sdk today. Diff vs merge-base reliable; may feed Lane B when requested. |

Lane A is default. Lanes B/C are not implied by cursor-sdk and are not required before re-dispatch.

## Commit posture

Commits happen only when operator asks, an agent chooses to checkpoint, or a named workflow defines commit/merge/release. Absence of commit does not mean incomplete, undeployed, or unsafe to build on. Sporadic master commits make `git diff` unreliable as a task/session summary.

## What not to infer

- ¬ uncommitted code ⇒ broken deploy or dead listener
- ¬ must commit before re-dispatch
- ¬ git-tracked ⇒ canonical
- ¬ dirty `git status` ⇒ reload failed because of pending edits
- ¬ `git diff` on master ⇒ accurate task/session scope

Use tree reads + Cortex + logs/events/live probes instead.

## Git diff reliability

| Substrate | `git diff` reliable? | Why |
|---|---|---|
| Lane A — live master checkout | No | No stable arc boundary; uncommitted edits, recent commits, and older `HEAD` mix. |
| Arc worktree (B/C; future cursor-sdk) | Yes | Diff vs merge-base is defined; Lane B fingerprints depend on it. |

On default substrate, answer "what changed?" by reading files and Cortex provenance, not `git diff`.

## LLM context — no diffs

Never submit git diffs, unified patches, or `git diff` output to LLMs for handoffs, consults, reviews, implement packets, or dispatch context. Lane A diffs are especially unreliable.

Provide whole files when bounded or relevant sections:

```text
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/…")
fs(sandbox="workspaces", op="md_read", path="universal-llm-gateway/…", section="…")
```

`git_diff` MCP exists for operator approval binding on arc worktrees (`diff_sha256` → integrate/land gates), not model context or master change-scope reconstruction. Use `include_full_diff=false` when only fingerprints are needed.

## Git CLI allowed only when

operator asks to commit/branch/PR; a named workflow defines commit/merge/release; or staging deliberate tracked-config/source change for rebuild-persistence. Otherwise do not reach for git.

## Related skills

- `lead-agent-git-integration` — arc `git_*` MCP tools (Lane B only)
- `architecture-invariants` — `[universal:git-posture]` one-liner in handoff Block 2
