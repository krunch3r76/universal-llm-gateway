<!-- target:* -->
# Session-close continuation handoff

Load when the operator requests a handoff on close (`with handoff`, `continuation handoff`, poll-delegation to a fresh session).

Companion: `session-close-handoff-depth-gate.md` (depth ∈ {light, verbatim}); kernel `agent-skills/session-close-kernel.md` § Step 6b.

## Invariant — transcript anchor (MANDATORY)

∀ `handoff_prompt` on `session_close`: the text MUST tell the **next** session how to load **this** closing session's transcript — not only the outstanding task (bus thread, todo, poll loop).

Boot does **not** auto-surface handoffs (assertion 8384). Without an explicit anchor, the next agent starts cold even though `transcript:{session_id}` exists.

**Required lines** (substitute `{session_id}` = closing session id, `{agent}` = seat slug):

```markdown
**Closing session:** `transcript:{session_id}`
**Load context:** `fs(cortex, op=read, path=notes/system/transcripts/{session_id}.md)`
  — or `cortex_boot(agent="{agent}", transcript_id="{session_id}")`
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

## Depth

| Operator signal | `transcript_depth` |
|---|---|
| Handoff requested | **`light`** minimum (never `none`) |
| Ceremonious / full walk-back | `verbatim` |

## Verified alternative (`handoff_source_path`)

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
<!-- /target:* -->
