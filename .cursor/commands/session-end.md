Close the current Cursor session by writing a Cortex session journal entry via
`session_close`. The verbatim transcript is assembled SERVER-SIDE from the
Cursor agent-transcripts JSONL — the agent supplies the path, never the
transcript content.

**Required rules (load before proceeding if not already in context).** Default
Cursor sessions do not auto-load the session-close stack. Read these in parallel
as Step 0:

- `../.cursor/rules/session-close.mdc` — six-step protocol + `transcript_depth` dial
- `../.cursor/rules/session-transcript-fidelity.mdc` — universal structural-layer discipline (verbatim depth only)
- `.cursor/rules/session-transcript-fidelity_ws.mdc` — workspace structural-layer canaries
- `../.cursor/rules/cortex-essentials.mdc` — cortex calling convention
- `../.cursor/rules/cortex-deep-ref.mdc` — full CRUD
- `fs(sandbox="cortex", op="read", path="agent-skills/session-close-kernel.md")` — **depth dial** (§ Depth dial; Cursor uses the same `transcript_depth` on `session_close`, JSONL only at `verbatim`)

(`provenance-discipline.mdc` and `agent-identity-signoff.mdc` are
always-applied; no need to re-load.)

---

## When to Use

Invoke when the **operator** signals close (`/session-end`, "close session",
"ceremoniously close"). The agent may suggest a stopping point but does not
auto-close (same operator-trigger framing as `agent-skills/session-close.md`).

**Web / API / subagent seats** follow that skill end-to-end (including
`transcript_md` assembly at `verbatim`). **Cursor** follows this command +
`session-close.mdc`; depth criteria are shared, mechanics differ (JSONL path
only when `transcript_depth="verbatim"`).

---

## Transcript depth (declare before close)

Pass `transcript_depth` on `session_close`. Default `verbatim` if omitted.
Full criteria: `agent-skills/session-close-kernel.md` § Depth dial.

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
the opposite artifact — a session-*opening* pointer that describes what the
*next* session should do (the `handoff_prompt` field exists to seed the next
session). A handoff is NOT a close trigger, and a session with no work has no
boundary to record.

Classify the triggering context before doing anything:

| Session state | Triggering message | Action |
|---|---|---|
| No / negligible work this session | Is a forward handoff / continuation ("first move on pickup…", "next session should…") | **Session OPEN.** Execute the handoff's work. Do NOT close. Do NOT prompt. |
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

**Skip this step** when `transcript_depth` is `light` or `none`.

```bash
ls -lt /home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts/ | grep '^d' | head -2
```

Take the most recently modified directory (current session). Note the
second-most-recent as the prior session — use its UUID's
session_id pattern when setting `prior_session_id`. The full JSONL
path is:

```
/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts/<uuid>/<uuid>.jsonl
```

¬ read the file. ¬ parse it. ¬ paste its contents anywhere.

### Step 0b: Resolve `session_id` (session **start** time — same as web-claude)

Priority order:

1. **`cortex_boot` `session_id`** — if boot ran this session, reuse that value
   (minute-resolution UTC at boot).
2. **JSONL creation time** — if no boot, derive from the Step 0 JSONL path
   (file birth time ≈ session start):

```bash
JSONL="/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts/<uuid>/<uuid>.jsonl"
AGENT="cursor"   # or claude-cursor / gpt-cursor per boot table
python3 -c "
import os
from datetime import UTC, datetime
from pathlib import Path
p = Path('''$JSONL''')
st = p.stat()
ts = getattr(st, 'st_birthtime', None) or st.st_mtime
print(f\"{'''$AGENT'''}-{datetime.fromtimestamp(ts, tz=UTC).strftime('%Y-%m-%d-%H%M')}\")
"
```

3. **Never** use `date -u +%Y-%m-%d-%H%M` at close — that mis-anchors
   `opened_at` and diverges from assertions seeded mid-session.

Optional: `cortex(tool="session_close_preflight", ...)` returns
`session_id_from_jsonl_start` and a timing warning when the supplied
`session_id` looks like close-time.

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

**Continues:** cursor-YYYY-MM-DD-HHmm  (if continuation — MANDATORY when applicable)

**Decisions:**
1. <decision one + why>
2. <decision two + why>

**Files modified:**
- path/to/file.py
- ...

**Open items:**
- <follow-up>

**Journal:** cursor-YYYY-MM-DD-HHmm
**Transcript:** cursor-YYYY-MM-DD-HHmm
```

Self-check:
1. Does `session_summary_md` start with `## Session Summary`?
2. Does `session_summary_md` contain `### User` blocks? (it should NOT — strip if so)
3. Is `**Continues:**` set when this session extends a prior?
4. Is the size <10 KB?

### Step 3: Call `session_close`

Set `transcript_depth` per the table above. Include `transcript_jsonl_path`
**only** when depth is `verbatim`.

```
cortex(tool="session_close", arguments='{
  "session_id": "cursor-YYYY-MM-DD-HHMM",
  "agent": "cursor",
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
| `session_id.invalid` | Match `cursor-YYYY-MM-DD-HHMM` |
| `session.already_closed` | SUCCESS-EQUIVALENT — quote IDs from detail object |
| `handoff.requires_transcript_entity` | Re-close with `transcript_depth="light"` (or `verbatim`); omit handoff only if arc complete |
| `handoff.missing_transcript_anchor` | Prepend the anchor block (`**Closing session:** transcript:{session_id}` + `**Load context:**` line) to `handoff_prompt`; re-call |

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
| Genuine in-flight work on the **same arc** (mid-implementation, open commitments, incomplete thread the operator intends to resume) | Pass `handoff_prompt` on the Step 3 `session_close` call with `transcript_depth` at least **`light`** (3–8 sentences + one executable first-action line). Present the same text to the operator after the Step 4 report line. |
| Session arc is complete (work landed, thread closed, nothing mid-flight) | Tell the operator there is nothing to continue. Omit `handoff_prompt`. Do NOT repackage unrelated open todos as a "continuation." |

Shape and quality bar: `agent-skills/session-close-handoff.md`. Depth: kernel § Depth dial.
The transcript anchor block is **server-enforced** — a `handoff_prompt` without
`transcript:{session_id}` (or the `notes/system/transcripts/{session_id}.md` path)
is rejected 422 `handoff.missing_transcript_anchor` (atomic rollback). Author the
anchor first, then the outstanding-work / next-step lines.

### Step 5: Post-close edge-first enrichment dispatch (async, non-blocking)

**Cursor gate — skip by default.** A default Cursor session is an IDE
coding session; the graph enrichment pass is noise overhead. **Skip
Step 5 entirely** UNLESS this session invoked
`cortex_boot(agent="cursor")`. Check the transcript / tool-call
history for an explicit `cortex_boot` call with `agent="cursor"`
before dispatching. If absent, end the close at Step 4.

Canonical protocol: `notes/system/shared/session-close-protocol.md`
§ Step 6.

Extract `entity_ids` from the `session_close` 201 response. The
enrichment pass is **edge-first** — sparseness gate is
`edge_count == 0`, not assertion count. Exempt: `todo:` and `task:` entities with
`workflow_state=open`.

Multi-todo arcs under a `project:` close via `entity_update` + closure assertions
on the project entity (phantom `task:` subsystem retired 2026-06-04).

(Stopgap `team_generate(agent="orion", ..., async=true)` dispatch
unchanged from the prior version of this file — its body remains the
same; only Step 3's call signature changed.)

---

## Quality Guides

**Summary**: Should orient the next session. "Implemented Phase 1 of
Cortex protocol integration — boot trigger, seed step, and session-end
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
