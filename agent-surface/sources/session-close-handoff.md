<!-- target:* -->
# Session-close continuation handoff

Load when the operator requests a handoff on close (`with handoff`, `continuation handoff`, poll-delegation to a fresh session).

Companion: `session-close-handoff-depth-gate.md` (depth ∈ {light, verbatim}); kernel skill `session-close-kernel` § Step 6b.

## Invariant — transcript anchor (MANDATORY)

∀ `handoff_prompt` on `session_close`: the text MUST tell the **next** session how to load **this** closing session's transcript — not only the outstanding task (bus thread, todo, poll loop).

Boot does **not** auto-surface handoffs (assertion 8384). Without an explicit anchor, the next agent starts cold even though `transcript:{session_id}` exists.

**Required lines** (substitute `{session_id}` = closing session id, `{agent}` = seat slug):

```markdown
**Closing session:** `transcript:{session_id}`
**Load context:** `fs(cortex, op=read, path=notes/system/transcripts/{session_id}.md)`
  — or `cortex_brief(seat="{agent}", transcript_id="{session_id}")`
```

Then § State at close, § Deferred inventory, § Await operator (see below). ¬ § Next
steps with imperatives.

## Invariant — handoff ≠ dispatch (MANDATORY)

A **continuation handoff** orients the **next** session — decisions, artifacts,
deferred inventory. It is **not** a work order.

| | **Handoff (continuation)** | **Dispatch (execution)** |
|---|---|---|
| Purpose | State at close + what remains open | Order work now |
| Authority | Operator's **next** explicit message | `/agent-bus {n}`, `team_dispatch`, implement packet, "this chat is thread X" |
| Pickup agent | Load anchor + transcript → **stop** → await operator | Act on the order |

**Authoring `handoff_prompt`:** state and pointers only. **Forbidden:** "first
action," "execute," "implement," "parallelize," "proceed to," poll/delegate
imperatives, multi-thread runbooks. **Required closing line:**
`**Await operator:**` — which thread/seat/slice (if any) is chosen in the
operator's **next** message, not inferred from the inventory.

**Deferred inventory** (not a todo list): name threads/todos/artifacts as nouns —
e.g. "thread 1340 — compact boot field 2; field 1 gated" — without instructing
the pickup agent to open or implement them.

Dispatch happens when the operator issues a **separate** execution directive
after pickup. Recency of bus threads is ¬ authority (see `handoff-pickup_ws.mdc`).

## Continuity efficiency (binding — operator 2026-07-20)

When the arc's continuity thread has **`spine=root`** (tag `role:root`, or CHECKPOINT
legacy read), `handoff_prompt` is a **pointer into that continuity root** — not a
paste of prior handoff bodies.

Required when `active_spine_root(S)`: root thread id · latest CHECKPOINT turn
(or stale flag) · scoreboard URI if chartered · 1-line state · next. Chat carries
the pointer; the next seat navigates the continuity root (`checkpoint-discipline` · `agent-bus-discipline` § R12 for done/close).

| Bad | Good |
|---|---|
| Paste prior `handoff_prompt` prose into the new handoff / chat | Cite root + CHECKPOINT turn; let the seat `get` that turn |
| Full tick-schema CHECKPOINT ceremony on a non-enrolled continuity root | Index-thin CHECKPOINT on continuity roots; full schema only when tagged `charter-runner` (tick-driven) |
| Leave finished worker windows open as “continuity” | Close `spine=work` worker threads; continuity root (or handoff pointer) is the spine |

Cross-ref: `checkpoint-discipline` (CHECKPOINT author/resume/profiles) · `agent-bus-discipline` § Standing root threads / R12 done-close.

## Roadmap position (forward half — when relevant)

A handoff's forward half MUST carry the active arc's **roadmap position** whenever
`write_handoff(S) ∧ in_flight_arc(S) ∧ active_work_has_parent_container(S)` — the active
`todo:`/`task:` is a child (or leaf) under a `project:` or `task:` container, or shares a
parent with ≥1 sibling work-item. A standalone parent-less, sibling-less todo has no
roadmap to show — omit the block (Deferred inventory already names it).

When relevant, in order:

1. **Top-level container** — the nearest enclosing `project:` (or `task:`): id + name.
2. **Active position** — where this arc sits inside it (`task:` and/or `todo:` id + name).
3. **Relevant siblings** — direct children of the nearest parent, **one hop**. Non-terminal
   (`open` / `in_progress` / `blocked`) listed in full; terminal (`done` / `cancelled`)
   collapsed to a count. ¬ traverse to a grandparent portfolio or unrelated projects.

∀ listed item: an explicit **status token** ∈ `{open, in_progress, done, blocked, deferred,
cancelled, unknown}` **and** a provenance marker:

- `(live)` — read this session from the entity's `workflow_state`.
- `(unknown)` — not read this session. A bare name with no status, or a *guessed* status, is
  non-compliant: unread ⇒ `unknown`, **never** an inference.

Cheapest live read: one `render_subgraph(root="{nearest_parent}", hops=1)` (or `entity_get`
per node) materializes parent + children + `workflow_state` in a single call. Verify at
minimum the container + active position; siblings MAY be `(unknown)` if unread, but say so.

**Roadmap ≠ Deferred inventory ≠ boot card.** Roadmap position = *where the work sits*
(status); Deferred inventory = *dispatches with operator actions*. They compose; neither
replaces the other. The block is justified despite the handoff∩boot-card-minimal redundancy
principle (assertion 11572) because a paste-forward handoff often lands in a **cold context
without this cortex's boot Arc digest** — the relevance predicate + one-hop scope keep it
from becoming a noisy inventory.

## Depth

| Operator signal | `transcript_depth` |
|---|---|
| Handoff requested | **`light`** minimum (never `none`) |
| Ceremonious / full walk-back | `verbatim` |

## Verified alternative (`handoff_source_path`)

Use the file-backed path not only when the body is long, but whenever the handoff contains embedded JSON, quotes, code fences, or a serialized command snippet (a `poll_hint` / `arguments_json` reference). Hand-escaping nested JSON into the inline `handoff_prompt` — which itself rides inside the `cortex(… arguments='…')` JSON string — is the cause of friction 17357.

Prefer file-backed derivation when the handoff body is long:

1. Append to `session_summary_md` (or a sidecar) a `## Handoff` section wrapped in:

   `<!-- handoff:continuation:start -->` … `<!-- handoff:continuation:end -->`

2. Pass `handoff_source_path=notes/system/transcripts/{session_id}.md` **only when** that file already exists pre-close, **or** use a pre-written cortex sidecar with markers.

3. `session_close` derives `handoff_prompt` server-side → `handoff_provenance.derivation=section` (verified surface).

## Inline handoff verification (write-time)

Inline `handoff_prompt` (no `handoff_source_path`) is auto-persisted to
`notes/system/handoffs/{session_id}.md` with `derivation=auto_persisted` —
file-backed for reload/tamper-detection, **not** the independently-authored
`section` tier (`verified` stays `derivation==section` only).

At close/upsert the server stamps `handoff_verification` on the transcript
attribute: `{checks:[{name,status,detail}], passed:N, total:M}` covering
transcript anchor, cited-entity resolvability, cited-entity state snapshot
(type/phase annotated), and `prompt_in_source` when a source path is also set.

**Deferred / planned entity refs:** `cited_entities_resolvable` passes when a
missing entity is explicitly marked deferred/planned (e.g. in **Deferred
inventory** or prose with "deferred", "planned", "not yet created"). Truly broken
refs (no deferral signal) still fail the check.

Boot renders the precomputed verification line. The "no confirmation writes"
gate keys on **failed checks** (`passed < total`), not the legacy `¬verified`
bit — a well-formed inline handoff with all checks passing boots without the
cold-distrust gauntlet. Prose stays suppressed (assertion 8384).

`handoff_prompt` without the anchor → **422 `handoff.missing_transcript_anchor`**
(atomic rollback, enforced pre-commit). Add the anchor block and re-call. A
`handoff_source_path` whose path already names the session satisfies the gate.

## Anti-patterns

| Bad | Good |
|---|---|
| Poll thread N only; no transcript ref (→ 422 `handoff.missing_transcript_anchor`) | Transcript anchor first, then state + deferred inventory |
| Repeat decisions/files from summary inline | Point at transcript file; handoff stays short |
| `transcript_depth=none` + handoff | `light` + handoff (422 otherwise) |
| "First action: `/agent-bus 1340`"; "parallelize"; "proceed to implement" (handoff as dispatch) | `**Await operator:**` + deferred inventory; dispatch is a later operator message |
| Multiple threads in one imperative runbook | Separate arcs named in inventory; one thread per operator-directed session |
| Bare roadmap inventory — names without status, or a guessed status | Every item carries a status token + `(live)`/`(unknown)`; unread ⇒ `unknown`, never inferred |
| Show only the active subtree when it sits under a project | Lead with nearest container + active position + one-hop relevant siblings |
| Inline `handoff_prompt` containing `{"thread":…,"poll_hint":…}` / `arguments_json` JSON | File-backed handoff (`handoff_source_path`); Deferred inventory cites `thread N` in prose only |
| Stack prior handoff bodies into the new `handoff_prompt` / chat paste | Pointer into spine=root thread (id + CHECKPOINT turn + scoreboard); navigate, ¬ re-embed |
<!-- /target:* -->
