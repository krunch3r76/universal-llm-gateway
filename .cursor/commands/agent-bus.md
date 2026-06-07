Fetch all unread agent-bus turns addressed to cursor and act on each sequentially.

All operations use `scripts/agent-bus` (direct UDS) — NOT `CallMcpTool`.
This avoids extension-host freezes on large payloads over SSH remote.

## Instructions

### 0. Cortex Boot (first invocation per session)

On the **first** `/agent-bus` invocation in a conversation, run the full
`/cortex-boot` procedure before fetching turns. This loads the TIER-2
cortex/agent-bus/team-collab rule stack (which is NOT auto-loaded in default
Cursor sessions) and then calls `cortex_boot`. Skip on subsequent invocations
within the same conversation — the rules are already in context.

If the TIER-2 rules are not yet loaded this session:

1. Read TIER-2 rules in parallel (per `.cursor/commands/cortex-boot.md` Step 1)
2. Then call:

```
CallMcpTool(server="user-vortex", toolName="cortex_boot", arguments={"agent": "cursor"})
```

If TIER-2 rules are already loaded, skip to the `cortex_boot` call directly.

Digest the boot narrative. Key items to internalize before acting:
- **Deadlines** — time-sensitive items shape priorities
- **Recent sessions** — continuity with what was last worked on
- **Open investigations** — suspected/hypothesized assertions to be aware of
- **Unread agent-bus turns** — preview of what's incoming
- **Review queue** — pending Cortex items that may be relevant

Do NOT repeat the full narrative to the user. Internalize and proceed.

### 1. Fetch All Unread Turns

```bash
scripts/agent-bus fetch-unread --to cursor --mark-read
```

### 2. Handle Result

**If no turns returned** (`turns: []`): report "No unread turns for cursor."

**If turns exist**: process each turn in order (oldest first). For each turn:
- Read the turn body and act on the instructions.
- The turn's `thread` field identifies the conversation thread.
- The turn's `from` field identifies who sent it.
- The turn's `body` contains the full message with instructions.
- Complete steps 3–5 (reply, thread state, Cortex seeds) for each turn before moving to the next.

**Check `_thread_info` before acting**: the response now always includes a
`_thread_info` block. If `has_earlier_turns: true`, fetch the full thread
before acting — the visible turns are missing context:

```bash
scripts/agent-bus fetch --thread THREAD_ID --unread --context 3 --mark-read
```

This fetches all unread turns plus 3 turns of prior context in one call.
If `_thread_info.has_earlier_turns` is still true after this, the thread is
long and the notice field contains the suggested `--last N` to widen further.

#### 2.0. Evaluate Model-Tier (operator-in-the-middle)

**Primary mechanism — user-supplied trigger.** If the invocation includes
a user-supplied tier identification (e.g. "you are running Sonnet 4.6"
or "you are Opus 4.8" preceding the `/agent-bus` invocation, in the same
or immediately prior message), apply the **blocking protocol** in
`model-tier-awareness.mdc` (User-Supplied Identity Trigger section):

1. Acknowledge identity.
2. Peek the turn (compact, no mark-read) — but do NOT investigate further.
3. Apply the literal pattern triggers (deprecate / refactor / restructure /
   redundant / claim about layer ownership / review / >1 rule file).
4. Emit the verdict block.
5. **Branch on verdict**: if SUITABLE, the supplied identity counts as
   proceed-confirmation — continue with the work in the same turn. If
   NOT SUITABLE, halt and wait for the user to confirm "proceed anyway"
   or switch and re-issue.

**Fallback — no user-supplied trigger.** Apply the rule's conditional
advisory note (non-blocking). Empirically the cheaper engine often
skips this; it is the best-effort path when the user hasn't gated.

Either way, the literal pattern triggers to check are:

- Does the body **propose to deprecate, remove, refactor, or restructure**
  an existing parameter / interface / component / rule / protocol?
- Does it use words like "redundant", "should be removed", "deprecate",
  "consolidate", "merge", "split", "rename API", "change contract"?
- Is its substance a **claim about correct architecture** (one mechanism
  is right, another is wrong; one layer should own X)?
- Does it ask for a **review, audit, or evaluation** of an existing design?
- Would acting on it require **changing more than one rule file** or more
  than one dispatcher spec?

If no triggers match: proceed directly to step 2a (no note needed).

#### 2a. Tool Execution Verification (when applicable)

If the inbound turn claims a tool was called (e.g. "I ran rag and got 5
results"), verify the claim via the Event Service before acting on the outcome:

```
observability(operation="verify-tool-execution", params={"signal_prefix": "mcp.rag.pipeline"})
```

The response shape: `{"verified": true/false, "signal_prefix": "...", "event": {...} | null}`.

| `verified` | Action |
|---|---|
| `true` | Proceed — the tool was actually called. Use `event.payload` for ground-truth metadata (content_length, duration_s, etc.) |
| `false` | Do NOT trust the claimed outcome. Report the discrepancy and re-run the tool yourself if needed |

**When to verify**: Cross-agent turns where the sender claims to have called an
MCP tool and the receiving agent needs to act on the result. Skip verification
for purely informational turns, self-notes, or turns that don't reference tool
execution.

**Signal prefix mapping** (common tools):

| Tool | Signal prefix |
|---|---|
| `rag(op="search")` / `rag(op="answer")` | `mcp.rag.pipeline` |
| `rag(op="coverage")` | `mcp.rag.coverage` |
| `pipeline` | `mcp.pipeline` |
| Any dispatch tool | `mcp.tool.dispatch` |

#### 2b. Section Operations for Large Documents

When a turn references a large structured document (spec, phase doc, runbook),
use the `fs` markdown ops for targeted access instead of whole-file reads:

```
fs(op="md_list",    sandbox="context", path="specs/foo.md")
fs(op="md_read",    sandbox="context", path="specs/foo.md", section="Task 3")
fs(op="md_replace", sandbox="context", path="specs/foo.md", section="API Design", content="...")
fs(op="md_append",  sandbox="context", path="specs/foo.md", section="Notes", content="...")
fs(op="md_delete",  sandbox="context", path="specs/foo.md", section="Obsolete Section")
```

**When to use**:
- Inbound turn says "update section X of tasks/specs/foo.md" → `md_read` + `md_replace`
- Resuming work on a phase doc → `md_list` for structural map, then `md_read` per task
- Composing a reply that summarizes a structured doc → `md_list` for structural summary

**Sandbox routing**: `sandbox="context"` for `tasks/` files, `sandbox="cortex"` for `/data/files`,
`sandbox="workspaces"` for files under `/mnt/torus/projects/{repo}/...` (paths must include repo prefix).

**Note**: there is no separate `markdown` MCP tool. All section-level operations are
exposed as `fs(op="md_*", ...)` ops on the standard `fs` tool.

### 3. Reply

After completing the work, post a reply turn.

For replies with markdown, backticks, or special characters (most replies),
write the body to a temp file first, then use `--body-file`:

1. Write body content to `/tmp/agent-bus-reply.md` using the Write tool
2. Post with:

```bash
scripts/agent-bus reply \
  --thread THREAD_ID \
  --to SENDER \
  --subject "SHORT_SUMMARY" \
  --body-file /tmp/agent-bus-reply.md \
  --after-turn TURN_NUMBER
```

Default message bodies remain briefings. Put long analysis, specs, and reviews
in a sidecar markdown file and link it from the turn. If the recipient is a web
agent that specifically needs inline long-form content, add `--allow-long-body`
to `post` or `reply`; this is an explicit override, not the default path.

For short plain-text replies, `--body` works inline:

```bash
scripts/agent-bus reply \
  --thread THREAD_ID \
  --to SENDER \
  --subject "SHORT_SUMMARY" \
  --body "Acknowledged. Working on it." \
  --after-turn TURN_NUMBER
```

Replace `THREAD_ID`, `SENDER`, `TURN_NUMBER` with values from the fetched turn.
`--after-turn` is the `turn_number` of the turn being replied to.
Optionally add `--supersedes-turn TURN_NUMBER` when this reply replaces an earlier turn (marks that turn as stale).

**Acknowledgement-only replies**: If the inbound turn is purely informational or
confirmatory and your reply is only an acknowledgement (no work requested, no
follow-up expected), reply and then close the thread immediately. Prefer this
over leaving behind open "thanks/acknowledged" threads.

When reporting to the user, cite `turn_number` (in-thread sequence), not `id` (global row ID).
Example: "Reply posted to thread 033, turn 2" — NOT "turn 82".

### 3a. Starting a New Thread

To create a **new** thread (auto-assigned numeric ID) and post the first turn:

```bash
scripts/agent-bus post \
  --slug "my-topic-slug" \
  --to RECIPIENT \
  --subject "SHORT_SUMMARY" \
  --body-file /tmp/agent-bus-reply.md
```

The response includes the auto-assigned thread ID. Use `reply` for subsequent
turns on that thread.

**NEVER** use `reply` to create a new thread — it requires a `--thread` ID and
will not auto-assign one. Always use `post` for new threads.

### 4. Update Thread State

After handling a turn (step 2) and replying (step 3), update the thread's
state file. **Each thread gets its own file** at
`tmp/thread-state/{ID}.md` — eliminates the duplicate-header / silent-Read-truncation
failure mode of the previous monolith approach.

Write the file (creates if missing, overwrites if present — no append fallback,
no duplicate-section risk):

```
fs(op="write", sandbox="workspaces",
   path="universal-llm-gateway/tmp/thread-state/{ID}.md",
   content="<!-- thread:{ID} -->\n# Thread {ID}: {slug}\n{state content}\n")
```

File content format:

```markdown
<!-- thread:{ID} -->
# Thread {ID}: {slug}
- **Latest turn**: {turn_number}
- **From**: {from} → **To**: {to}
- **Subject**: {subject}
- **What was requested**: {1-2 sentence summary of the inbound turn}
- **What was done**: {1-3 sentence summary of actions taken}
- **Files touched**: {list of files created/modified/deleted, or "none"}
- **Decisions made**: {key decisions, or "none"}
- **Follow-ups identified**: {deferred work, known gaps, or "none"}
- **Thread status**: {open | closed}
```

The HTML comment `<!-- thread:{ID} -->` is preserved as a machine-readable
anchor for ad-hoc scans, even though the filename now also encodes the ID.

At session start, use `fs(op="list", sandbox="workspaces", path="universal-llm-gateway/tmp/thread-state")`
to get a thread overview without reading any individual file. To inspect a
specific thread, `fs(op="read", ...)` the corresponding file directly.

**Why per-thread files**: a single monolith grows unbounded (one section per
interaction), accumulates duplicate `<!-- thread:{ID} -->` headers when an
append fallback runs after a partial md_replace failure, and trips the Read
tool's silent truncation past ~570 lines. Per-thread files make each write
idempotent (`fs(op="write", ...)` overwrites cleanly) and let the agent see
exact file contents without mystery truncation.

Skip this step for `--peek` invocations.

### 5. Seed Cortex Assertions (if decisions were made)

After the debrief, check whether the "Decisions made" field contains substantive
architectural or implementation decisions. If so, seed Cortex assertions.

Skip this step if:
- Decisions field is "none" or trivially mechanical (e.g. "renamed variable")
- The thread was purely informational (no implementation work done)
- The turn was a `--peek` invocation

For each decision worth recording:

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "assert",
  "arguments": "{\"entity_id\": \"decision:SHORT-SLUG\", \"claim\": \"WHAT was decided and WHY, including the alternative rejected\", \"confidence\": \"confirmed\", \"evidence\": \"Implemented in agent-bus thread THREAD_ID\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"confidence_score\": 0.95, \"seeded_by\": \"cursor\", \"reasoning_summary\": \"WHY this was the right choice over alternatives\"}"
})
```

**Derivation-type note**: use `inference` for session-originated decisions
(agent synthesis from prior context). `compression` and `quotation` are
reserved for ingested-document-derived claims and **require** `chunk_id` —
the cortex-api rejects them with HTTP 422 otherwise.

**Date-pattern note**: if the claim contains a date (`YYYY-MM-DD`, ISO timestamp,
or named date like "2026-04-30"), include `valid_from` in the arguments
(e.g. `"valid_from": "2026-04-30T00:00:00Z"`) — the cortex-api requires it
for non-observation derivation types when a date pattern is detected.

**Claim quality guide** — the claim must capture reasoning, not just action:

| Bad claim (what only) | Good claim (what + why) |
|---|---|
| "Changed routing to use UDS" | "Chose UDS over TCP for internal relay because it eliminates a Docker network hop and simplifies container networking" |
| "Added parallel restart" | "Implemented parallel service restart via asyncio.gather because managed services are independent — sequential restart added unnecessary latency" |
| "Created /session-end command" | "Added /session-end command to trigger Cortex journal writes at conversation close — the tool existed but no protocol trigger enforced its use" |

If the decision entity doesn't exist yet, create it first:

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "entity_create",
  "arguments": "{\"id\": \"decision:SHORT-SLUG\", \"type\": \"decision\", \"name\": \"Human-readable decision title\"}"
})
```

### 5a. Seed Surprises (if anything non-obvious happened)

After seeding decisions, check whether the implementation hit anything unexpected:
a workaround for an undocumented behavior, a version collision, a missing
migration, a config assumption that turned out wrong, a failure mode not
covered by existing rules or lessons.

Skip if the implementation was routine — only seed genuine surprises that would
save the next session from re-discovering the same thing.

Seed on the relevant `service:*` or `doc:*` entity (not on `decision:*` — surprises
are about the system, not the choice):

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "assert",
  "arguments": "{\"entity_id\": \"service:SERVICE-NAME\", \"claim\": \"WHAT was surprising and WHY it matters for future work\", \"confidence\": \"believed\", \"evidence\": \"Discovered during agent-bus thread THREAD_ID\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"confidence_score\": 0.85, \"seeded_by\": \"cursor\", \"reasoning_summary\": \"WHY this matters for future sessions\"}"
})
```

**Surprise quality guide** — the claim must be actionable, not just observational:

| Bad (observation only) | Good (actionable for next session) |
|---|---|
| "Migration failed" | "Migration version 005 was already used by a clean-slate migration — always check schema_version table for actual last version, not just ls migrations/" |
| "Container rebuild was needed" | "Code changes to cortex-api entity routes aren't picked up until container rebuild — docker compose cached layers but the DB migration still needs to run on startup" |
| "Config was wrong" | "cortex_boot is a standalone MCP tool — use CallMcpTool toolName=cortex_boot, not the unified cortex() dispatcher" |

### 5b. Seed Continuation State (always, unless session is exploratory)

Always seed a `believed` assertion capturing the current state of work on the
thread's primary entity. This is the single highest-value boot item — it tells
the next session "where we left off" without re-orientation.

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "assert",
  "arguments": "{\"entity_id\": \"decision:ENTITY-SLUG\", \"claim\": \"WHAT is done, WHAT is next, WHAT is blocking (if anything)\", \"confidence\": \"believed\", \"evidence\": \"Session state as of agent-bus thread THREAD_ID\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"confidence_score\": 0.9, \"seeded_by\": \"cursor\"}"
})
```

**Supersede previous continuation state**: If you seeded continuation state for
the same entity in a previous session (or earlier in this session), supersede it
so boot only surfaces the latest:

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "supersede",
  "arguments": "{\"old_assertion_id\": PREVIOUS_ID, \"entity_id\": \"decision:ENTITY-SLUG\", \"claim\": \"UPDATED continuation state\", \"confidence\": \"believed\", \"evidence\": \"...\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"session_id\": \"SESSION_ID\", \"agent\": \"cursor\"}"
})
```

`session_id` and `agent` are **required** for `supersede` (creates a session edge).
Use the `session_id` from `cortex_boot` response (e.g. `cursor-2026-03-31-1145`).

If you don't remember the previous assertion ID, just seed a new one — boot
can deduplicate by recency. Don't let perfect supersede tracking block the seed.

**Continuation state guide**:

| Bad (vague) | Good (actionable pickup) |
|---|---|
| "Work in progress" | "Phase 1 complete (boot trigger, seed step, /session-end). Phase 2: run for 1 week, assess compliance rates and claim quality, then decide on subagent delegation for seeding" |
| "Some things are done" | "cortex-api rebuilt with migration 006 (content_hash). MCP server still needs rebuild to pick up cortex.py entity_create/entity_update changes from thread 130" |
| "Need to finish the refactor" | "ServiceController refactor landed (phase 1). Phase 2 plan at tmp/prompts/manage/phase2.md — parallel restart via asyncio.gather. User has reviewed and staged phase 1 files" |

### 5c. Wire Todo Edges (if this thread spawned a todo)

If a todo was **created or promoted** during this thread, wire its context edges
before the thread closes. If ≥2 todos form one deliverable arc, use a `project:`
entity + spec and graph edges — not a master-`todo:` (`task:` retired 2026-06-04).
Prose in `description` does not satisfy the seed
contract — the `todo_implementation_seed_incomplete` gate checks graph-traversable
relationships, not keywords.

**Signature** — `relationship_create` takes **`source_id`** (from), **`target_id`** (to),
and **`type_id`** (the relationship type). It does NOT take `from_entity` / `to_entity` /
`type` — those are the SQL column names, not the tool params, and passing them yields
`source_id is required`. The `type_id` must be a **registered** type or you get
`Relationship type not found: <type>`. Valid structural types include: `related_to`,
`references`, `depends_on`, `blocked_by`, `owns`, `parent_of`, `child_of`, `sibling_of`,
`requires`, `supplement_to`, `evidence_for`. (`relates_to` is NOT registered — use
`related_to`.)

**For each todo spawned this thread:**

1. `references → decision:*` — every decision made in-session that the todo depends on:
```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "relationship_create",
  "arguments": "{\"source_id\": \"todo:SLUG\", \"target_id\": \"decision:SLUG\", \"type_id\": \"references\"}"
})
```

2. `related_to → service:*` (or other substrate entity) — if the todo targets a known service or code path:
```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "relationship_create",
  "arguments": "{\"source_id\": \"todo:SLUG\", \"target_id\": \"service:SLUG\", \"type_id\": \"related_to\"}"
})
```

3. Thread sidecar (evidence) — the agent-bus thread that originated the todo. Prefer
   capturing the thread as an `evidence_uris` **attribute** on the todo's assertions
   (see step 5b); if wiring a structural edge to a thread entity, use `references`:
```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "relationship_create",
  "arguments": "{\"source_id\": \"todo:SLUG\", \"target_id\": \"agent-bus:THREAD_ID\", \"type_id\": \"references\"}"
})
```

4. `requires → agent_skill:*` — one edge per `required_skills` entry (mirrors the attribute at the graph layer):
```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "relationship_create",
  "arguments": "{\"source_id\": \"todo:SLUG\", \"target_id\": \"agent_skill:SKILL-SLUG\", \"type_id\": \"requires\"}"
})
```

**Skip this step** if no todo was spawned in this thread, or if the todo was
created with `attributes.backlog=true` (backlog-only; gate is suppressed).

## Variants

| Invocation | Behavior |
|---|---|
| `/agent-bus` | Fetch all unread turns to cursor, act on each sequentially |
| `/agent-bus {thread}` | Fetch all unread turns in a specific thread, act on each |
| `/agent-bus {thread} --all` | Fetch ALL turns in a specific thread (read and unread) |
| `/agent-bus --peek` | Fetch but do NOT mark read or act — just show the turn |
| `/agent-bus --status` | Show thread list |

### `/agent-bus {thread}`

```bash
scripts/agent-bus fetch --thread {thread} --unread --context 3 --mark-read
```

### `/agent-bus {thread} --all`

```bash
scripts/agent-bus fetch --thread {thread} --mark-read
```

### `/agent-bus --peek`

```bash
scripts/agent-bus fetch --to cursor --last 1 --unread --compact
```

### `/agent-bus --status`

```bash
scripts/agent-bus threads --status active
```

## Direct CLI Reference

```bash
# Fetch (inbox or thread)
scripts/agent-bus fetch-unread --to cursor --mark-read
scripts/agent-bus fetch --thread 049 --unread --context 3 --mark-read
scripts/agent-bus fetch --thread 049 --last 5
scripts/agent-bus fetch --thread 049 --last 1 --compact

# Post (new thread — auto-assigns numeric ID)
scripts/agent-bus post --slug "my-topic" --to web --subject "Title" --body-file /tmp/body.md
scripts/agent-bus post --slug "my-topic" --to web --subject "Title" --body "Short body"

# Reply (existing thread only)
scripts/agent-bus reply --thread 049 --to web --subject "done" --body-file /tmp/reply.md --after-turn 6
scripts/agent-bus reply --thread 049 --to web --subject "ack" --body "Acknowledged." --after-turn 6

# Threads
scripts/agent-bus threads
scripts/agent-bus threads --status closed

# Update thread
scripts/agent-bus update-thread --thread 049 --status closed --summary "Entity resolution shipped"
```

## Thread Closure Protocol

**Invariant**: closed threads MUST have `unread_count == 0`.

When closing a thread, `update-thread --status closed` automatically marks all
turns as read. This keeps the closed-thread list free of stale unread noise.

### Self-closing note (common)

Agent posts a closing note to itself (e.g. "Verified — closing") that no one
needs to read. Close the thread — auto-mark-read handles it:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Verified — closing" \
  --body "Confirmed. Closing." --after-turn TURN_NUMBER
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..."
```

Alternatively, mark the reply itself read immediately:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Verified — closing" \
  --body "Confirmed. Closing." --after-turn TURN_NUMBER --mark-read
```

### Closing note for another agent

If the note IS intended for the recipient, do NOT close the thread. The
recipient reads the turn and closes:

```bash
# Sender: post note, leave thread open
scripts/agent-bus reply --thread THREAD_ID --to RECIPIENT --subject "Summary for review" \
  --body "..." --after-turn TURN_NUMBER

# Recipient: read the turn, then close
scripts/agent-bus fetch --thread THREAD_ID --last 1 --mark-read
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..."
```

### Acknowledgement-only reply (default)

If the inbound turn needed no action beyond a short acknowledgement, reply and
close the thread in the same pass:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Acknowledged" \
  --body "Acknowledged." --after-turn TURN_NUMBER
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "Acknowledgement sent; no further action required."
```

Use this when the thread is effectively complete after the acknowledgement.
Do not use it if the recipient still needs to read your substantive reply.

### Opting out of auto-mark-read

In rare cases where you need to close without marking turns read:

```bash
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..." --no-mark-read
```
