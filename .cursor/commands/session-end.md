Close the current Cursor session by writing a Cortex session journal entry via
`session_close`. The verbatim transcript is assembled SERVER-SIDE from the
Cursor agent-transcripts JSONL — the agent supplies the path, never the
transcript content.

**Required rules (load before proceeding if not already in context).** Default
Cursor sessions do not auto-load the session-close stack. Read these in parallel
as Step 0:

- `../.cursor/rules/session-close.mdc` — six-step protocol + `transcript_depth` dial (Cursor SOT)
- `../.cursor/rules/session-transcript-fidelity.mdc` — universal structural-layer discipline (verbatim depth only)
- `.cursor/rules/session-transcript-fidelity_ws.mdc` — workspace structural-layer canaries
- `../.cursor/rules/cortex-essentials.mdc` — cortex calling convention
- `../.cursor/rules/cortex-deep-ref.mdc` — full CRUD
- Use the `session-close-kernel` skill — trigger/editorial + **seat-routed** procedure (Cursor = `session_close`, not life `close(op=…)`)

¬ `fs(… agent-skills/session-close*.md)` — cortex `agent-skills/` mirror is retired (D3). Depth dial for Cursor lives in `session-close.mdc`.

(`provenance-discipline.mdc` and `agent-identity-signoff.mdc` are
always-applied; no need to re-load.)

---

## When to Use

Invoke when the **operator** signals close (`/session-end`, "close session",
"ceremoniously close"). The agent may suggest a stopping point but does not
auto-close (same operator-trigger framing as skill `session-close-kernel`).

Knowledge-graph seeding (decisions, surprises, continuation state, todo edges)
now happens here during session close — see the appendix below.

**Life / web** uses MCP `close(op=stage|draft|check|commit)` per the kernel skill.
**Cursor** follows this command + `session-close.mdc` with
`cortex(tool="session_close")` — do not call `close(op=…)` from the code seat.
Depth criteria are shared; Cursor mechanics differ (JSONL path only when
`transcript_depth="verbatim"`).

---

## Transcript depth (declare before close)

Pass `transcript_depth` on `session_close`. Kernel: plain `/session-end` ⇒
`light`; ceremonious/full ⇒ `verbatim`. Omit → server default may differ —
**always declare explicitly**.

Full criteria: `session-close.mdc` depth dial + §0-depth (kernel skill points
Cursor here).

| Depth | Typical Cursor sessions | JSONL Step 0? |
|---|---|---|
| `verbatim` | Multi-domain reasoning, novel decisions, operator said **ceremoniously close**, enrichment wanted | **Yes** — path required |
| `light` | Operator requested **handoff** (minimum), or bus+summary worth indexing without turn walk-back | **No** — omit `transcript_jsonl_path` |
| `none` | Mechanical close, **no** `handoff_prompt` / `handoff_source_path`; bus/todo already durable | **No** — omit `transcript_jsonl_path` |

**Handoff gate:** `handoff_prompt` or `handoff_source_path` ⟹ depth **`light`** or **`verbatim`** only.
Server 422: `handoff.requires_transcript_entity` if `none` + handoff.

`light` / `none` still run steps 2–6 in `session-close.mdc` (assertions, thread 480,
journal row). Only server-side JSONL assembly and large transcript files are skipped.

---

## Instructions

### Step 0a: Substantive-work precondition (MANDATORY — before any close action)

A `session_close` is **atomic and irreversible** (`UNIQUE(session_id)`): a
spurious or accidental `/session-end` trigger spends the one-shot `session_id`
on a junk journal row that cannot be reverted, only superseded after the fact.

A close records **work this session performed**. A **continuation handoff** is
the opposite artifact — a session-*opening* pointer that **orients** the next
session (state + deferred inventory; **handoff ≠ dispatch**). The
`handoff_prompt` field seeds it. A handoff is NOT a close trigger, NOT a work
order, and a session with no work has no boundary to record.

Classify the triggering context before doing anything:

| Session state | Triggering message | Action |
|---|---|---|
| No / negligible work this session | Is a forward handoff / continuation | **Session OPEN.** Load handoff context; await operator dispatch. Do NOT close. Do NOT prompt. |
| No / negligible work this session | Bare close trigger, nothing to record | Say there is nothing to close; do NOT fire the irreversible close. Do NOT prompt. |
| Substantive work done | Clear stopping point / explicit close | Proceed to Step 0 (close normally). |
| Substantive work done | Ambiguous whether this is a stopping point | **Only here**: confirm close-vs-continue with the operator. |

The slash-command envelope (`--- Cursor Command: session-end ---`) is delivered
in the `<cursor_commands>` context block, not the operator's typed prose — it is
indistinguishable from an accidental trigger. A continuation paste in a no-work
session is almost always a session *open*; pick up the work, do not close, and
do not interrogate the operator with a confirmation dialog. Reserve the
confirmation prompt for the single ambiguous row above.

**`<user_query>` is the sole close authority.** When `<cursor_commands>` carries
`/session-end` on every turn but `<user_query>` does not request close, treat the
envelope as sticky composer noise — ignore silently, do not mention unprompted,
do not load this command's rule stack. Operator fix: clear the pinned slash
command in the composer or start a fresh chat (often caused by appending freeform
text after `/session-end` in one input — Cursor replays the compound invocation
for the rest of the thread). See `session-close.mdc` §0a-i.

### Step 0: Resolve the JSONL path (`verbatim` only)

**Declare `transcript_depth` before any filesystem work.** Plain `/session-end`
⇒ `light` (kernel). Ceremonious/full ⇒ `verbatim`.

**Skip this step entirely** when `transcript_depth` is `light` or `none` — do
**not** `ls` agent-transcripts; omit `transcript_jsonl_path`. Go to Step 0b.

**Metadata-first (MANDATORY).** When the harness exposes `transcript_id`, build:

```
$HOME/.cursor/projects/<workspace-slug>/agent-transcripts/<transcript_id>/<transcript_id>.jsonl
```

Cross-check picked UUID against conversation `transcript_id` before any `ls -lt`
fallback.

**Fallback** (metadata often absent — Cursor does not always inject `transcript_id`
into context; that is expected, not a discovery failure):

```bash
ls -lt $HOME/.cursor/projects/<workspace-slug>/agent-transcripts/ | grep '^d' | head -3
```

Hollow-transcript gate — reject candidates with **< 20 lines**:

```bash
wc -l $HOME/.cursor/projects/<workspace-slug>/agent-transcripts/<uuid>/<uuid>.jsonl
```

Pick the newest passing candidate. Second passing candidate (by mtime) →
`prior_session_id` when continuing; or use `prior_session_id_suggestion` from
preflight.

¬ title-grep / multi-dir walk to find the chat by subject. ¬ read the file.
¬ parse it. ¬ paste its contents anywhere.

### Step 0b: Resolve `session_id` (preflight-first)

Format: `{agent}-YYYY-MM-DD-HHMMSS-{3hex}` (UTC session **start**, not close).

Priority order:

1. **`cortex_brief` `session_id`** — reuse when `cortex_brief` ran this session.
2. **`session_close_preflight`** — canonical when no boot-held ID. **Not an
   ID-only probe** — `summary` + `session_summary_md` are required (placeholders
   OK for the ID-resolution call). Example:

```
cortex(tool="session_close_preflight", arguments='{
  "session_id": "<best-guess>",
  "agent": "cursor",
  "transcript_jsonl_path": "<PATH FROM STEP 0>",
  "session_summary_md": "## Session Summary\\n\\n**Decisions:** placeholder",
  "summary": "Preflight session_id anchor check."
}')
```

   Use returned copy-paste **`session_id`** when present, else
   `session_id_from_jsonl_start`. Optional: `prior_session_id_suggestion`.

   **`light` / `none` depth + no boot-held ID (id-derivation ≠ archival depth):**
   Step 0 was skipped, so you have no `<PATH FROM STEP 0>` — but preflight is the
   only server-side source of a correct-format `session_id`, and preflight
   **writes nothing**. Resolve the transcript JSONL path (command Step 0
   metadata-first / `ls -lt` fallback) and pass it as `transcript_jsonl_path`
   **on the preflight call only** to obtain `session_id_from_jsonl_start`; then
   **omit** `transcript_jsonl_path` on the real close (keeps the light/none
   archival contract). Do NOT hand-construct the ID from the `<timestamp>` —
   dropping the seconds or `-{3hex}` suffix yields `session_id.invalid` and a
   retry loop (recurrence class: friction 23135 / `cursor-2026-07-16` light
   close). If you cannot obtain the JSONL, the ID template is
   `cursor-YYYY-MM-DD-HHMMSS-{3hex}` at UTC session **start** (six-digit `HHMMSS`,
   three-hex suffix — both mandatory; e.g. `cursor-2026-07-16-194435-519`).
3. **Never** `date -u` or `stat`/`st_mtime` shell one-liners at close — unsafe on
   Linux (mis-anchors `opened_at`). Preflight derives from JSONL `<timestamp>`
   when present.

### Step 0c: Todo closure gate (before `session_close`)

**When**: substantive close (passed Step 0a) and this session executed work on
one or more `todo:` entities.

1. **Inventory** todos touched this session (pickup, `/todo`, spec implementation,
   `entity_get` during the arc).
2. **Per todo** still `open` or `in_progress`:
   - Work complete with no follow-ups scoped to that todo → run
     `pipeline(op="run", pipeline_id="todo-close", ...)` **before** Step 3.
     See `todo_ws.mdc` / `session-close.mdc` §0b for options shape.
   - Work incomplete → leave open; put follow-ups in Step 1 `open_items` with
     `[todo:slug]` ref.
   - Appears done but ambiguous → **prompt the operator** to close or confirm
     leaving it open; do not call `session_close` until resolved.
3. **Invariant**: executed todo ∧ acceptance met ∧ ¬todo-scoped follow-ups ⟹
   close via `todo-close` before session close.
4. After closes, include every `todo:` whose `workflow_state` changed in
   `entity_ids` on the Step 3 `session_close` call.

Already `done`/`cancelled` → list in `entity_ids` only.

### Step 0d: Unproved go-live (before `session_close`)

When this session opened a go-live (`go live` / `everything live?` / an armed
`restart_intent_id`) and proof has not closed: close is allowed; `open_items`
MUST carry `restart_intent_id` (or `none`) plus claim class
`drain_pending|live|not_live|live@sha`. Do not narrate activation.
`commit` / this close do not open or complete go-live.
SOT: `session-close.mdc` §0d · `restart-drain-discipline` § Go-live proof loop.

### Step 1: Synthesize (Summary Artifact)

Review the current conversation. Identify:

- **Summary**: 2-3 sentence overview of what was accomplished (≥20 chars)
- **Domains**: which areas were touched (e.g. `rag`, `routing`, `cortex`)
- **Decisions**: architectural or implementation decisions made (list)
- **Open items**: unfinished work, known gaps, or follow-ups (list)

This is the compression artifact for the `summary`, `decisions`, and
`open_items` fields.

### Step 2: Compose the structural layer (`session_summary_md`)

The verbatim layer is server-assembled — do NOT walk the context
window pasting turns. Compose ONLY the structural layer:

```markdown
## Session Summary

**Continues:** cursor-YYYY-MM-DD-HHMMSS-{3hex}  (if continuation — MANDATORY when applicable)

**Decisions:**
1. <decision one + why>
2. <decision two + why>

**Files modified:**
- path/to/file.py
- ...

**Open items:**
- <follow-up>

**Journal:** cursor-YYYY-MM-DD-HHMMSS-{3hex}
**Transcript:** cursor-YYYY-MM-DD-HHMMSS-{3hex}
```

Self-check:
1. Does `session_summary_md` start with `## Session Summary`?
2. Does `session_summary_md` contain `### User` blocks? (it should NOT — strip if so)
3. Is `**Continues:**` set when this session extends a prior?
4. Is the size <10 KB?

### Step 2.5: Attestation via `doc_validate` (mandatory before real close)

`session_close` rejects with 422 `session_close_validate_attestation_missing` without prior PASS attestation. **Skip for `dry_run=True` only.**

Call `doc_validate` with `doc_type="session_close"` and the **same flat kwargs** as the eventual close — not `text`/`path`/`source_ref` (those are for `implement_dense_spec`). Required fields: `session_id`, `agent`, `session_summary_md`, `summary`.

```
cortex(tool="doc_validate", arguments='{
  "doc_type": "session_close",
  "session_id": "cursor-YYYY-MM-DD-HHMMSS-{3hex}",
  "agent": "cursor",
  "session_summary_md": "<STRUCTURAL LAYER FROM STEP 2>",
  "summary": "<SUMMARY FROM STEP 1>",
  "transcript_depth": "none"
}')
```

On `status=pass`, capture `attestation_tokens` (includes `session_close_validate:pass` and `session_id:{session_id}`). Remediate any failed preflight/audit gates before proceeding.

### Step 3: Call `session_close`

Set `transcript_depth` per the table above. Include `transcript_jsonl_path`
**only** when depth is `verbatim`.

```
cortex(tool="session_close", arguments='{
  "session_id": "cursor-YYYY-MM-DD-HHMMSS-{3hex}",
  "agent": "cursor",
  "validate_attestation": ["session_close_validate:pass", "session_id:cursor-YYYY-MM-DD-HHMMSS-{3hex}"],
  "transcript_depth": "none",
  "session_summary_md": "<STRUCTURAL LAYER FROM STEP 2>",
  "summary": "<SUMMARY FROM STEP 1>",
  "domains": ["DOMAIN1"],
  "decisions": ["DECISION1"],
  "open_items": ["ITEM1"],
  "entity_ids": ["entity:slug"],
  "prior_session_id": "<PRIOR SESSION ID or omit>"
}')
```

Use the exact `attestation_tokens` array from Step 2.5 — do not hand-compose tokens.

For `verbatim`, add `"transcript_depth": "verbatim"` and
`"transcript_jsonl_path": "<PATH FROM STEP 0>"`.

Capture from the 201 response (always):
- `journal_row_id`
- `transcript_depth` (echoed)

When depth is `verbatim` or `light`, also capture:
- `transcript_entity_id`
- `transcript_path`
- `content_hash` (sha256:<hex>)
- `turn_count`
- `byte_count`

When depth is `none`, `transcript_entity_id`, `transcript_path`, and
`content_hash` are null — journal row is the durable record.

**Optional `handoff_prompt`:** include on the `session_close` call only when
the operator explicitly requests a continuation handoff in the same message
(see § Continuation handoff below). Default: omit.

### Step 3b: 422 handling

| Reason | Fix |
|---|---|
| `transcript_jsonl.invalid` | Re-run Step 0; path may have been wrong or relative-to-nothing |
| `session_summary.invalid` | Re-do Step 2 — ensure `## Session Summary` heading |
| `transcript.hollow` | Path points at a tool-only or empty JSONL — wrong UUID |
| `transcript.missing_structure` | Structural layer too thin; add Decisions/Files/Open items |
| `summary.too_short` | Extend summary to ≥20 chars |
| `session_id.invalid` | Re-run Step 0b preflight; use returned `session_id` (`cursor-YYYY-MM-DD-HHMMSS-{3hex}`) |
| `session.already_closed` | SUCCESS-EQUIVALENT — quote IDs from detail object |
| `handoff.requires_transcript_entity` | Re-close with `transcript_depth="light"` (or `verbatim`); omit handoff only if arc complete |
| `handoff.missing_transcript_anchor` | Prepend the anchor block (`**Closing session:** transcript:{session_id}` + `**Load context:**` line) to `handoff_prompt`; re-call |
| `session_close_validate_attestation_missing` | Run Step 2.5 `doc_validate(doc_type="session_close", …)`; pass `attestation_tokens` as `validate_attestation` |
| `session_close_validate_session_mismatch` | `validate_attestation` must include `session_id:{session_id}` matching the close call |

Maximum one retry on non-`already_closed` 422.

### Step 4: Report

Quote response-payload IDs. Shape depends on `transcript_depth`:

**`verbatim` or `light`:**

```
Session closed — transcript:{session_id} (depth={transcript_depth}, journal_row_id={N}, content_hash=sha256:{prefix}…, thread-480 turn {T})
```

**`none`:**

```
Session closed — {session_id} (depth=none, journal_row_id={N}, thread-480 turn {T}; no transcript entity)
```

¬ re-read the transcript file. For `verbatim`, `content_hash` is sufficient verification.

### Step 4b: Continuation handoff (operator-request only)

**Default: skip.** Do NOT compose, present, or paste a continuation handoff
prompt unless the operator explicitly requests one in the same message as
`/session-end` (e.g. "with handoff", "write a continuation handoff",
"handoff for next session").

When requested:

| Condition | Action |
|---|---|
| Genuine in-flight work on the **same arc** (mid-implementation, open commitments, incomplete thread the operator intends to resume) | Pass `handoff_prompt` on the Step 3 `session_close` call with `transcript_depth` at least **`light`** (state + deferred inventory + **Await operator**; **handoff ≠ dispatch**). Present the same text to the operator after the Step 4 report line. |
| Session arc is complete (work landed, thread closed, nothing mid-flight) | Tell the operator there is nothing to continue. Omit `handoff_prompt`. Do NOT repackage unrelated open todos as a "continuation." |

Shape and quality bar: Use the `session-close-handoff` skill. Depth: `session-close.mdc` dial.
The transcript anchor block is **server-enforced** — a `handoff_prompt` without
`transcript:{session_id}` (or the `notes/system/transcripts/{session_id}.md` path)
is rejected 422 `handoff.missing_transcript_anchor` (atomic rollback). Author the
anchor first, then state/deferred inventory/**Await operator** (no poll/delegate
imperatives).

### Step 5: Post-close edge-first enrichment dispatch (async, non-blocking)

**Cursor gate — skip by default.** A default Cursor session is an IDE
coding session; the graph enrichment pass is noise overhead. **Skip
Step 5 entirely** UNLESS this session invoked
`cortex_brief(agent="cursor")`. Check the transcript / tool-call
history for an explicit `cortex_brief` call with `agent="cursor"`
before dispatching. If absent, end the close at Step 4.

Canonical protocol: `notes/system/shared/session-close-protocol.md`
§ Step 6.

Extract `entity_ids` from the `session_close` 201 response. The
enrichment pass is **edge-first** — sparseness gate is
`edge_count == 0`, not assertion count. Exempt: `todo:` and `task:` entities with
`workflow_state=open`.

Multi-todo arcs grouped under a `task:` or `project:` close via `entity_update` (workflow_state→done) + closure assertions on the container entity. (Only the `task-seed`/`task-close` *workflow* was retired 2026-06-04 — the `task:` entity type is live, `decision:task-subsystem-retired`.)

(Stopgap `team_generate(agent="orion", ..., async=true)` dispatch
unchanged from the prior version of this file — its body remains the
same; only Step 3's call signature changed.)

---

## Quality Guides

**Summary**: Should orient the next session. "Implemented Phase 1 of
Cortex protocol integration — continuity-brief trigger, seed step, and session-end
command added to /agent-bus workflow" is useful. "Worked on stuff" is
not.

**Decisions**: Same quality bar as Cortex assertion claims — capture
the *why*.

**Open items**: Actionable next steps.

**session_summary_md**: Structural layer ONLY. If you find yourself
pasting verbatim turns here, STOP — the server assembles them.

---

## Rules

- **Three separate artifacts**: synthesis (summary/decisions/open-items),
  structural layer (`session_summary_md`), and JSONL path. Do not
  conflate them.
- The agent NEVER constructs the verbatim transcript — server does it.
- One `session_close` per session.
- Include ALL domains touched, even briefly.
- `journal_write` is **deprecated** — always use `session_close`.
- **Continuation handoffs are opt-in.** Never auto-compose or present a
  handoff prompt on `/session-end` unless the operator explicitly requests
  one. Unrelated open todos are not continuations.

---

## Knowledge-graph seeding (decisions · surprises · continuation · todo edges)

Run these steps during session close when the session produced substantive
decisions, surprises, continuation state, or spawned todos. Skip individual
steps per the skip conditions below.

### Seed Cortex Assertions (if decisions were made)

After the debrief, check whether the "Decisions made" field contains substantive
architectural or implementation decisions. If so, seed Cortex assertions.

Skip this step if:
- Decisions field is "none" or trivially mechanical (e.g. "renamed variable")
- The thread was purely informational (no implementation work done)

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

### Seed Surprises (if anything non-obvious happened)

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
| "Config was wrong" | "cortex_brief is a standalone MCP tool — use CallMcpTool toolName=cortex_brief, not the unified cortex() dispatcher" |

### Seed Continuation State (always, unless session is exploratory)

Always seed a `believed` assertion capturing the current state of work on the
thread's primary entity. This is the single highest-value continuity item — it tells
the next session "where we left off" without re-orientation.

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "assert",
  "arguments": "{\"entity_id\": \"decision:ENTITY-SLUG\", \"claim\": \"WHAT is done, WHAT is next, WHAT is blocking (if anything)\", \"confidence\": \"believed\", \"evidence\": \"Session state as of agent-bus thread THREAD_ID\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"confidence_score\": 0.9, \"seeded_by\": \"cursor\"}"
})
```

**Supersede previous continuation state**: If you seeded continuation state for
the same entity in a previous session (or earlier in this session), supersede it
so the briefing card only surfaces the latest:

```
CallMcpTool(server="user-vortex", toolName="cortex", arguments={
  "tool": "supersede",
  "arguments": "{\"old_assertion_id\": PREVIOUS_ID, \"entity_id\": \"decision:ENTITY-SLUG\", \"claim\": \"UPDATED continuation state\", \"confidence\": \"believed\", \"evidence\": \"...\", \"evidence_uris\": [\"agent-bus:THREAD_ID\"], \"derivation_type\": \"inference\", \"session_id\": \"SESSION_ID\", \"agent\": \"cursor\"}"
})
```

`session_id` and `agent` are **required** for `supersede` (creates a session edge).
Use the `session_id` from `cortex_brief` response (e.g. `cursor-2026-03-31-1145`).

If you don't remember the previous assertion ID, just seed a new one — boot
can deduplicate by recency. Don't let perfect supersede tracking block the seed.

**Continuation state guide**:

| Bad (vague) | Good (actionable pickup) |
|---|---|
| "Work in progress" | "Phase 1 complete (boot trigger, seed step, /session-end). Phase 2: run for 1 week, assess compliance rates and claim quality, then decide on subagent delegation for seeding" |
| "Some things are done" | "cortex-api rebuilt with migration 006 (content_hash). MCP server still needs rebuild to pick up cortex.py entity_create/entity_update changes from thread 130" |
| "Need to finish the refactor" | "ServiceController refactor landed (phase 1). Phase 2 plan at tmp/prompts/manage/phase2.md — parallel restart via asyncio.gather. User has reviewed and staged phase 1 files" |

### Wire Todo Edges (if this thread spawned a todo)

If a todo was **created or promoted** during this thread, wire its context edges
before the thread closes. If ≥2 leaf todos form one bounded, closable arc, group them under a `task:`
entity via `child_of` (`agent_skill:task-grouping-discipline`); a larger umbrella
over multiple arcs/plans uses a `project:` entity + spec and graph edges. Either
way, not a master-`todo:`. (Only the `task-seed`/`task-close` *workflow* was retired
2026-06-04 — the `task:` entity type is live, `decision:task-subsystem-retired`.)
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
   (see continuation seeding above); if wiring a structural edge to a thread entity, use `references`:
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
