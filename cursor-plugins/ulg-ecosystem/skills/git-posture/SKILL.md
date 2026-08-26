---
description: "On coding sessions — file existence, canonicality, authorship, done-ness, git CLI, or cursor-sdk implement substrate truth before inferring state."
---

# Git Posture & Truth Substrate

## When to read

Read on:
- "does X exist / is X canonical / is this mine / is this done?"
- before git CLI or `git_*` MCP on the shared checkout;
- before inferring service failure from uncommitted or dirty git state;
- before cursor-sdk implement dispatch or git-integration-worker diagnostics;
- when handoffs, consults, reviews, or packets touch repo state.

Default substrate: **attended Cursor IDE** edits on the live shared checkout
(`GIT_INTEGRATION_SOURCE_REPO`, default `universal-llm-gateway`). **cursor-sdk
generate** defaults to **Lane B** when the caller **passes** `lane="B"` (regime
on, in-repo). Omit is not that default: empty `files_expected` + omit → Lane A.
Explicit `lane="A"` or out-of-repo stays on the shared checkout. Caller recipe:
`consult-routing` § cursor-sdk checkout lane. These rules bind unless the
operator directs otherwise.

**Sole-checkout corollary:** this seat assumes one live shared `master`
working tree and **¬intersecting parallel writers**. Do **not** `git stash`
or otherwise isolate the tree to A/B against clean HEAD or to “protect”
phantom peers — read the on-disk tree; treat out-of-scope test/git noise as
pre-existing.

## Shared checkout concurrency (G6 — operator bind 2026-08-04)

Evidence: `todo:concurrency-policy-honesty` gate refused at N=1 (agent-bus:6792).
These binds describe **tested reality**, not aspiration.

| Bind | Statement |
|---|---|
| **Authorized-but-off** | Operator multi-writer on Lane-A is **authorized by policy** (`CURSOR_SDK_OPERATOR_MULTI_A_ENABLED`) and **disabled in practice** — effective limit held at **1** while F-1 (collision detection), F-2 (read-isolation fence), and F-3 (sound authorship attribution) remain open. Do not blur this into “supported” or “forbidden.” |
| **Path-explicit commit** | On the shared checkout, staging is **path-explicit** — `git_commit(paths=[…])` / path-scoped `git add`. **`git add -A` and any whole-tree stage defeat the safe API** even when `commit_paths` is correct in isolation; a safe API plus an unsafe habit is an unsafe system. Name the defeat explicitly. |
| **Supersede under multi-writer** | When **more than one live write lease** is active, supersede **refuses to restore** peer paths and reports **`unrevertable`** — cancel is not a clean undo while peers are running. |
| **Scoped counts** | Any scalar that is scoped or projected (**`write_capacity`**, **`tree_residue`**, closeout **`shipped`** with failing gates, propagation **`proof`** pre-filled before execution) must **publish its scope** in the same surface. A number whose evidence contradicts its label is a defect, not a display bug. |
| **Board posture ≠ writer count** | SDK board classes **`parallel`**, **`nested`**, and **`id_split`** name **relationship among live rows**, never concurrent **writer count**. Writer census = ledger **`live_writers`** / **`active_by_lane`** only (`id_split` legend already states this — treat it as the standing rule). |

## Invariant — truth substrate is on-disk tree + Cortex + live process, not git

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

## Deploy / live vs commit (BINDING — operator 2026-08-01)

**Commit status is not what makes code executable at any stage.** Propagation /
`sync_restart` of **non-committed** working-tree edits **is live** on this fleet
when the process has been restarted — the load event reads filesystem source, not
`git checkout`.

| Stage | What actually runs |
|---|---|
| **cursor-sdk dispatch (Lane B default)** | Isolated Lane-B worktree on disk (`cursor-sdk/lane-{thread}`) |
| **cursor-sdk dispatch (`lane="A"` or out-of-repo)** | Live shared checkout on disk |
| **Attended Cursor IDE** | Live shared checkout on disk |
| **Host services** (`git_integration_worker`, `cortex_api`, `stargate`, `rag`, …) | `sync_restart` respawns a subprocess with `PYTHONPATH` pointed at the checkout |
| **Gateway** | Bind-mounted source in the container |
| **MCP** | `docker cp` from workspace into `/app`, then restart — still filesystem source, not `git checkout` |

`landed ≠ live` means **the process has not been restarted yet** — **not** “the
change isn’t committed.” A `sync_restart` picks up whatever is on disk at restart
time, committed or not.

Commit enters only for explicit git workflow (`git_integrate` / `git_land`, arc
worktrees, closeout HEAD attribution). It is **not** the gate between “edited”
and “running.”

There is one stronger reporting class: `live@<sha>`. Use it only when the
deployment paths were committed path-explicitly **before** the attributed
restart, the restart and health probe completed, and the live probe shows
`code_ref_satisfied` (equal or ancestor) plus process identity movement. Disclose
relevant dirty paths or served-ahead-of-HEAD state; do not imply exact clean
attribution. A dirty-tree restart remains ordinary `live` and remains legal.

A later commit does not upgrade an earlier dirty-tree `live` to `live@<sha>`.
That class needs a **new** recycle after the path-explicit commit. Finishing
work **is** go-live (`restart-drain-discipline` proof loop): path-explicit
commit of the work paths + recycle of every serving process + graph stamp.
A mid-arc checkpoint `commit` and `/session-end` are not work-complete
(`decision:go-live-proof-loop`).

**Anti-patterns this kills:** treating uncommitted-but-restarted code as illicit
“live-ahead-of-HEAD”; refusing to propagate because tree is dirty; building FATAL
gates that equate `served ≠ git HEAD` with a broken fleet when the shared
checkout is intentionally dirty and was restarted. Served-vs-HEAD deltas on a
dirty live checkout are **topology-expected**; ownership / handoff of foreign WIP
is a separate courtesy problem, not proof that live-without-commit is defective.

Doctrine: `decision:checkout-disk-is-executable`.

## Default implement lane

| Surface | Where work lands | Git protocol |
|---|---|---|
| cursor-sdk generate (regime on, in-repo, `lane="B"`) | Lane-B worktree (`cursor-sdk/lane-{thread}`) | Commit on the lane branch; declare `land_disposition` on closeout |
| cursor-sdk generate (`lane="A"` or out-of-repo) | Live shared checkout | Path-explicit commit on `master` when checkpointing |
| Attended Cursor IDE | Live shared checkout | No standing workflow. On-disk tree = truth. Commits sporadic; `git diff` unreliable. |

`lane=` is required on top-level cursor-sdk generate except `nest_under` /
`resume_of` inherit. Empty `files_expected` + **omit** selects Lane A
(`select_lane` `opt_out`) — do not headline omit as Lane B. Caller recipe:
`consult-routing` § cursor-sdk checkout lane.

## Stay on one designated tree (Lane B)

Reuse the **same** Lane-B worktree when **any** predicate holds:

| Predicate | Meaning |
|---|---|
| `nest_under` | Nested dispatch inherits the parent's worktree |
| `resume_of` | Resume dispatch inherits the parent's worktree |
| same `thread_id` + `lookup_lane_worktree` | Prior dispatch on this lane already minted a tree |

**One arc, one tree** — sibling dispatches on the same arc share a branch/worktree
unless the packet explicitly starts fresh. **One todo, one tree** is the default
for unrelated todos. Same-file parallel work on one arc defaults to
`nest_under`/reuse, not two trees plus land-time merge.

## `git_*` MCP (headless only)

`seat ∈ Cursor_IDE ⇒ ¬git_*_mcp` — editor apply + routine commits instead.

When the operator directs headless `git_*` tools (relay → `git-integration-worker`):

| Tool | Use |
|---|---|
| `git_commit` | **Default.** Path-explicit commit on the live shared checkout. Never `--all`. |
| `git_status` / `git_diff` | Fingerprints / dirty check when needed for a gated call |
| `git_integrate` / `git_land` | Operator-gated merge primitives only when the operator has an arc to land — not the default implement path |

**`git_integrate`/`git_land` first-call footguns (friction, agent-bus:7323 F1/F8):** both require
the worktree to be checked out on a branch literally named `arc/{arc}`
(`libs/git_integrate/validate.py` — `expected_branch = f"arc/{arc}"`) plus positional
`approval` + `expected_diff_sha256` args; neither call creates that branch or worktree.
Mint it explicitly before the first call: `git worktree add -b arc/<slug>
<worktree-path> <source-branch>`. If the worktree was torn down or never materialized
between gate-prep and the land call, the first call 404s `worktree_missing` — remint
from the branch and retry.

**`git_commit` recipe (live checkout):**
1. `git_commit(worktree_path=<live master checkout>, expected_branch="master", paths=[…], dry_run=True)` → `expected_paths_sha256` + numstat
2. Operator reviews → `git_commit(…, approval, expected_paths_sha256, commit_message=…)`

`approval ⇔ fingerprint`. Re-run dry_run if paths/diff changed. `¬git_land` / `¬git_integrate` for ordinary master checkout work — those verbs are merge primitives, not “commit on master.”

Authority: `decision:lead-agent-git-integration` (atomic gated primitive). Routine IDE/shell commits of reviewed work remain ungated policy (`core_ws` §User Approval).

## Commit posture

Commits happen only when operator asks, an agent chooses to checkpoint, or a
named workflow defines commit/merge/release. Absence of commit does not mean
incomplete, undeployed, or unsafe to build on. Sporadic master commits make
`git diff` unreliable as a task/session summary.

**Named workflow — lane A regular commit (6642):** cursor-auto ↔ operator-proxy
closeouts carry a fail-closed ``checkpoint:`` disposition
(``committed <sha> paths=N`` | ``nothing_authored`` | ``deferred: <reason>``).
Commit is the attribution-clearing act: path-explicit from the episode
authored-path set (dispatch ``wt_baseline`` delta), never ``--all``, never
foreign WIP. ``tree_residue: N`` counts dirty paths not in that set. Commit
is disclosure on closeout, not a propagate/restart/done gate; ``deferred:`` stays
legal forever.

## Branch ownership — a lane retires its own branch

`∀ Lane-B lane: mint(branch) ⇒ own(branch) until discharged`.

Everything above says commits are not gates. Branches are different: a Lane-B
lane's branch is a **standing obligation**, because nothing else can retire it.
The lead lands lane work from the shared checkout with its own commits, so
`git cherry` never marks the branch patch-equivalent and merged-ancestry GC
never fires — the branch outlives every sweep unless its lane discharges it.

Two honest exits, both archive-backed (`refs/tags/archive/*`), neither
destructive:

| Exit | Declare | Verified by |
|---|---|---|
| Landed | `land_disposition: landed` | Content probe against `master` — assertion is not accepted |
| Discarded | `land_disposition: discard` + `land_reason:` | The recorded reason |

Silence opens an attributed **branch debt** carried in the dispatch ledger,
shown to whoever dispatches into that lane next. Aged debt escalates on the
owning bus thread; at the hard horizon the lane's Lane-B admit is refused.
Nothing is deleted on a timer — sweeping aged residue would destroy the
evidence and clear the owner, which is the failure this replaced.

Discharge anytime: `POST /cursor-sdk/branch-discharge`
`{"branch": …, "verb": "landed"|"discard", "reason": …}`. Read standing:
`GET /cursor-sdk/branch-debt` or `lane_hygiene` in `manage busy_status`.
Full obligation text: `dispatch-report-discipline` § Branch discharge.

## What not to infer

- ¬ uncommitted code ⇒ broken deploy or dead listener
- ¬ must commit before re-dispatch
- ¬ git-tracked ⇒ canonical
- ¬ dirty `git status` ⇒ reload failed because of pending edits
- ¬ `git diff` on master ⇒ accurate task/session scope
- ¬ need `git stash` / clean-HEAD compare to diagnose a test or peer conflict

Use tree reads + Cortex + logs/events/live probes instead.

## Git diff reliability

On the live shared checkout, `git diff` is **not** a reliable task/session summary — uncommitted edits, recent commits, and older `HEAD` mix. Answer "what changed?" by reading files and Cortex provenance.

Never submit git diffs, unified patches, or `git diff` output to LLMs for handoffs, consults, reviews, implement packets, or dispatch context.

Provide whole files when bounded or relevant sections:

```text
fs(sandbox="workspaces", op="read", path="universal-llm-gateway/…")
fs(sandbox="workspaces", op="md_read", path="universal-llm-gateway/…", section="…")
```

`git_diff` MCP is for operator approval fingerprints on gated `git_*` flows (`include_full_diff=false` when only hashes are needed) — not for model context or master change-scope reconstruction.

Life/CDP catch-up is `fs(op="recent_commits")` (oneline subjects, no diffs) — not `git_log`, not a project index. Its HEAD and `authored_at` fields are a deprecated, lower-confidence timestamp fallback during migration; they never produce a validated-live claim. For “is the service running this commit?” use the dual-surface `fleet_liveness(code_ref=...)` query. `git_*` remains life-banned.

## Git CLI allowed only when

operator asks to commit/branch/PR; a named workflow defines commit/merge/release; or staging deliberate tracked-config/source change for rebuild-persistence. Otherwise do not reach for git.

## Cursor IDE seat scope (folded from `commit-and-git-scope`)

`seat ∈ Cursor_IDE ⇒ ¬use(git_* MCP tools ∨ raw git CLI)` in normal work. Those are for headless seats when the operator directs them.

| Bad (Cursor IDE) | Good |
|---|---|
| `git stash` / `git checkout -- <file>` to inspect/revert | read traceback; editor undo / revert UI |
| `git stash` to A/B vs clean HEAD | read the tree; sole shared `master` |
| `git_commit` / `git_land` mid-session | operator-attended apply; commit only if asked |

`∀ seat: ¬{git checkout -- ., git checkout -- <dir>, git reset --hard, git clean -fd, git stash(unowned_work)}`.
No-force: `¬push --force` and `¬history_rewrite` on shared branches unless operator explicitly requests.

**Anti-pattern — "uncommitted" as a risk trigger.** Hearing "uncommitted" / "dirty working tree" is NOT a durability signal — on-disk is already real/durable/done. Do NOT reach for `git_status` / `git_diff` / `git_*` to "check working-tree state" on that basis. Read this skill FIRST whenever git state is mentioned, before touching any git tool.

## Related skills

- `architecture-invariants` — `[universal:git-posture]` one-liner in handoff Block 2
- `shared-checkout-housekeeping` — parallel dirty/WIP on sole shared `master` is baseline noise, not disposable; closeout dual-channel deviations surface census without false partial (friction a:25030)
- Former `commit-and-git-scope` — **merged here** 2026-07-11 (`todo:skill-pool-dedupe`)
- Former `lead-agent-git-integration` — **retired** 2026-07-15; `git_*` MCP discipline lives in this skill
