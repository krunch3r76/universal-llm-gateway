## Gotchas

### When Lane A is still the right call

Lane B is the default, including a cortex note, a read-only plan, and
`sdk_mode=plan`. A throwaway worktree is enough. Lane A is the exception, and
a seat does not pass `lane="A"` because the work writes nothing or to avoid
minting a tree. Name Lane A only when the operator explicitly requests shared
master, or when `CURSOR_LANE_B_SCOPE_REFUSED` cites paths outside the repo and
those paths cannot be moved onto the tree. Quote that request or refusal.

### `CURSOR_LANE_B_WORKTREE_MISSING` — B without a tree is not A

`lane="B"` always requires a minted or inherited worktree. Nesting
`lane="B"` under a Lane-A parent (shared-master lease) is **422**, not a
relabel. Omit `lane=` on a nest to inherit the parent's isolation; pass
`lane="A"` only with a named reason. Do not set `CURSOR_SDK_REFUSE_B_WITHOUT_WORKTREE` —
refusal is unconditional.

### `CURSOR_LANE_B_SCOPE_REFUSED` ≠ license to omit `lane=`

**Failure class (7281 / 7286, 2026-08-15):** Operator-bound Lane B. Explicit
`lane="B"` refused with `CURSOR_LANE_B_SCOPE_REFUSED` ("files_expected contains
paths outside source_repo"). Lead retried **without** `lane=` so the conductor
would admit. GIW defaulted to **Lane A** (shared master write lease). Meanwhile
the G3 Composer candidate sat on `cursor-sdk/lane-7312`. Result: conductor and
implementation on **two checkouts** — master vs working branch split.

| Wrong | Right |
|---|---|
| Omit `lane=` to "get past" the refusal | Fix the packet / derived `files_expected` so every path is repo-relative under `source_repo`, then re-admit with `lane="B"` |
| Proceed after admit without reading `active_by_lane` | Confirm Lane B worktree before nesting Composer or editing |
| Land by `git checkout <branch> -- <paths>` onto master | Merge the lane branch via `git_land` (see `git-posture`) — the mission's own admit is the standing merge ack; land on green, don't re-ask |

Scope refuse usually means an absolute mount path, `cortex://` / non-repo URI, or
a path outside the gateway checkout leaked into machine-derived file scope.
Strip those from the conductor packet (and any `source_ref` materializer inputs)
until `lane="B"` admits cleanly.

Nested Composer under a Lane-B conductor must `nest_under=<conductor dispatch_id>`
**and** inherit Lane B — ¬ fire a fresh top-level implement that can mint another
branch or fall onto master.

### Self-nest routing — seat-identity preamble exposes your `dispatch_id`

Lane-B `conductor` conductor missions receive a **seat-identity preamble** from
GIW `resolve_prompt_preamble` when the dispatch is a genuine conductor packet:
either **`packet_path`** is set (IDE `team_dispatch`) **or** the body carries the
mandatory literal line ``Use the conductor skill — …`` (message-body
``COMMISSION_CONDUCTOR`` via `team_dispatch` — no `packet_path`).
The preamble names your own GIW **`dispatch_id`** (short ledger id — **not** the
Stargate execution UUID on bus `to:`) and the two nesting paths:

| Path | When | Shape |
|---|---|---|
| **(a) Independent dispatch** | Judgment/spec-only work that will **not** land on this mission's branch (investigate, confer, dense spec bind) | Separate `team_dispatch` **without** `nest_under` |
| **(b) Mechanical landing** | G-rows whose code must merge on this mission branch | `team_dispatch(..., nest_under=<your dispatch_id>)` so the child inherits Lane B |

**Never** substitute independent dispatch for `nest_under` on mechanical landing work —
that mints another branch or falls onto master. Historical class: conductor absorbs
~1k+ SLOC in-seat (7407) when it cannot discover its own id.

Nested implement packets: omit `todo:` front-matter when the parent already holds that
work-identity — repeating it 409s `CURSOR_SOURCE_REF_IN_FLIGHT`.

### Refuse-and-close != caution — flag it, still execute

**Failure class (7419, 2026-08-16 — friction 29694/29693):** a Composer conductor
read the full mission (28 read-only tool calls, zero files touched) then
closed `status: partial` / `work_outcome: checks_failed` because, in its own
words, it wanted to "lay out the concrete plan and flag that merge step
explicitly before doing anything, rather than just executing straight
through." The packet named no hold-merge exception — § Run to completion
already grants standing authorization for exactly that merge. The conductor
invented an exception nobody named, and made zero progress while inventing
it: it neither nested Composer nor touched the mechanical G-rows leading up
to the flagged step.

The seat-identity preamble now restates this at the exact point it exposes
the conductor's own `dispatch_id` (`_CONDUCTOR_RUN_TO_COMPLETION_TEMPLATE` in
`cursor_sdk_packet.py`) — the reminder is in-context at dispatch time, not
only in this skill file. Further fixes fold under
`task:conductor-self-nest-routing` — do not mint a parallel todo.

