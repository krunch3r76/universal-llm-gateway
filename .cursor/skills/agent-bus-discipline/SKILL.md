---
name: agent-bus-discipline
description: "Mechanics discipline for agent_bus operations: new-thread vs reply decision, pre-flight turn probing, large-payload navigation, sidecar body pattern, thread lifecycle."
trigger_short: "agent_bus thread ∨ reply ∨ sidecar"
skill_category: dispatch-delegation
trigger_match_terms: ["agent-bus-discipline", "agent_bus_discipline", "agent_bus", "thread", "reply", "sidecar", "dispatch-delegation", "mechanics", "operations", "new-thread"]
---

# Agent Bus Discipline

**Authority:** universal — applies on any `agent_bus` operation in any Cursor IDE
session whose checkout is the ULG hub **or** a ULG-ecosystem satellite
(¬ hub-only / ¬ workspace-specific).

## New thread vs reply

Default to a new thread unless the message is a direct continuation of an existing exchange.

| Situation | Op |
|---|---|
| New task, even same topic | `send(new_slug=...)` |
| Direct reply to a specific turn, same task/live exchange | `send(thread=...)` |
| Follow-up after prior agent responded | `send(thread=...)` |

Topic overlap ≠ thread continuity. Prior thread can be evidence context; new task still gets new thread. `from_agent` is required. Legacy `post`/`reply` are deprecated; prefer `send` where available.

## Dispatch polling

After `team_dispatch` / handoff with `poll_hint`, poll using `agent_bus(wait, thread=N, after_turn=T, completion="first_reply_from", from_agent=...)`.

```python
agent_bus(tool="wait", arguments=poll_hint.arguments_json)
```

`wait` is one server-side short-block call; re-call to keep polling. `wait_seconds ≤ 60`
(0 = snapshot). Prefer `poll_hint.arguments_json` — Cursor-IDE seats get
`wait_seconds=0`; web/API keep 60 (friction 24081). Attended spinner >2 min on
wait → interrupt and re-poll with `wait_seconds=0`. `complete=true` means the
qualifying turn exists, not that findings were applied.

### cursor-sdk closeout polling

For `seat=cursor-sdk` dispatches (`op=generate`): poll with `completion="first_reply_from"` and `from_agent="cursor-sdk"` from `poll_hint` — the machine closeout always posts under the `cursor-sdk` seat label.

`¬` infer completion from subject matching `closeout|complete`. Machine closeouts use subjects like `cursor-sdk dispatch {dispatch_id}`; terminal state is in the JSON body (`status ∈ {complete, partial, failed}`, plus `effects_manifest` / evidence pointers). After `complete=true`, `get` the qualifying turn and parse the body — do not re-poll on subject tokens.

Human-authored closeouts may coincidentally include `closeout` in the subject; treat that as incidental, not a protocol signal. Full arc context: `orchestrator-workflow` § Post-dispatch poll recipe.

## Pre-flight before reply

Use `get(thread, turn_number="latest")` when you have not fetched this thread in-session. On `409 unread_turns_exist`, read `latest_turn_number` from the error body and retry with that value as `after_turn` — no probe-downward loop.

`deliverable | GO | pointer | CHECKPOINT` post to thread A for work governed by dispatch/consult thread B ⇒ `fetch_unread(thread=B, mark_read=true)` immediately before send. An earlier in-session read does not satisfy this gate.

```python
agent_bus(tool="get", arguments='{"thread":"THREAD_ID","turn_number":"latest"}')
# use returned turn_number as after_turn on send(thread=...)
```

`409` means stale `after_turn` or unread turns remain; use `detail.latest_turn_number` and mark-read remediation before retry.

`transport_or_connector_error ∧ ¬http_protocol_response ⇒ retry_once`; repeated failure ⇒ escalate.

## Large-payload navigation

If `fetch(last=1, compact=true)` overflows, do not skip the thread. Use `get(turn_number="latest")` or `get(turn_number=N)` to fetch one turn at a time.

## Standing root threads

High-turn-count roots fail by many turns, not large bodies. `¬ linearly_read(root_thread) for state`.

**CHECKPOINT author / resume / tip hygiene / profiles:** Use the `checkpoint-discipline` skill (schema field IDs: `cortex://notes/system/specs/checkpoint-schema-profiles.md`). This skill keeps bus mechanics + R12 done/close below.

State reconstitution default = tip CHECKPOINT + roadmap (+ scoreboard when chartered). Completeness projection = scoreboard (graph canonical). `empty(Next-pickup) ⇏ arc_complete`. **Done-claim gate:** § R12. Templates: `charter-scoreboard.md` · `continuity-doc.md`.

### Orchestration birth gate (binding — operator 2026-07-30)

```
∀ spine=root (orchestration thread): charter_exists ∧ objective_bound
```

**Charter** here is distinct from **`charter-runner` enrollment** (machine tick). Manual orchestration (`orchestrator_continuity`) still requires a charter; enrollment only selects the CHECKPOINT profile (`tick_charter` vs `orchestrator_continuity`).

| Surface | Required when | Minimum |
|---|---|---|
| **Objective** | always on birth | one bound sentence (`Objective:`, `## Anchor`, or `Primary OPEN:`) |
| **Scoreboard** | deliverable sequence ∨ `charter-runner` enrolled | `cortex://…/<thread-id>-charter-scoreboard.md` indexed in CHECKPOINT |
| **Continuity doc** | manual root without scoreboard yet | `continuity-doc` or `Charter: cortex://…` pointer + objective |

**Birth order (do not invert):** (1) mint charter surfaces (scoreboard and/or continuity doc) → (2) post birth CHECKPOINT indexing them + concrete Next-pickup → (3) stamp `role:root` / enroll when belt path applies.

**Close / disposition forbids on newborn roots:**

| Situation | Disposition | ¬ |
|---|---|---|
| Just established orchestration | `leave-open+reason` (charter birth in flight) | `advise-close` |
| Empty / ungated Next-pickup on birth | incomplete birth — fix charter surfaces | arc complete |
| Session close with open root | report disposition fields | treat as permission to declare done |

Server posts a non-blocking `briefing_advisory` (`reason=root_missing_charter`) on birth/bootstrap CHECKPOINTs that lack charter surfaces — fix before relying on the root.

Doctrine: `orchestrator-core` § Standing root threads · `orchestrator-workflow` R12.

Session close for active root must include root thread id, roadmap path, latest checkpoint or stale flag, active item ids/seats, child root ids, and scoreboard path when applicable. Root required for multi-session, multi-seat, or multi-wave arcs; exempt one-pass work with no durable sequencing.

**Endeavor dual-surface pattern (binding — operator 2026-07-15, agent-bus:5129).** Life/web work uses **exactly two** bus surfaces — do not mint a third “letter vs corpus” split:

| Surface | Who | What |
|---|---|---|
| **Endeavor root** | cursor + operator | CHECKPOINT, scoreboard pointers, child registry, closeout summaries. Completeness **projection** = scoreboard (graph canonical). ¬ web rewrite traffic. |
| **One life side channel** | web-anthropic (+ cursor packets) | Everything web must **act** on for this endeavor: interrogate, rewrite, nit binds, lock claims; corpus admits/harvest when web is the actor. Samples/INDEX are **file context** (pseudo-RAG), not a second bus thread. |

**Push rule:** operator pushes the **side channel** when web’s next turn is expected. Do not push the endeavor root for INFO/orchestration notes.

**Incorporation:** side-channel closeout → short pointer on root **and** scoreboard write-back. API generate/reply plumbing stays off the endeavor root (R11 / R12).

**Anti-pattern:** spawning separate children for “letter” vs “corpus” while both are web-anthropic acts on the same endeavor — that recreates push confusion (grounded: mistaken 5148 split).

## Thread classification (thin)

Two axes only. Spec: `docs/specs/agent-bus-thread-classification-thin.md`.

| Axis | Values | Recognition |
|---|---|---|
| **Spine** | `root` \| `work` | Tag `role:root` ⇒ root; default (absent) ⇒ work. Legacy read: CHECKPOINT turn ∧ ¬`type:monitor` ⇒ root until stamped. |
| **Enrollment** | `charter-runner` \| none | Dual-key unchanged (`enroll_charter_runner=true` to newly add). **Constraint:** enrolled ⇒ spine root (write gate auto-stamps `role:root`). |

Only reserved spine tag: `role:root`. Other `role:*` tags are rejected on write. Not classification: `bus_lifecycle:*`, DB `status` / `bus_lifecycle_state`, facet tags (`type:*`, `project:*`, …), thread `480`.

CHECKPOINT profile still follows enrollment only (`tick_charter` iff enrolled; else `orchestrator_continuity`).

## Session-close thread disposition

**When:** substantive close ∧ session used `agent_bus`.

**Shared enum (all branches):** `advise-close` | `closed` | `leave-open+reason`

Disposition derives from **spine** (+ 480 special) — not a parallel taxonomy:

| Spine / special | Close report block |
|---|---|
| **root** (standing continuity) | At minimum `advise-close` + root thread id, roadmap path, latest checkpoint turn or stale flag, active item ids/seats, child root ids, scoreboard path when chartered — **unless** orchestration was just birthed and charter surfaces are still incomplete ⇒ `leave-open+reason` (see § Orchestration birth gate); `advise-close` is a disposition report, not arc-complete authority |
| **work** (one-off / dispatch / window) | Exactly one enum value + thread id |
| **Thread 480** (`agent-activity-journal`) | Debrief reply only — **not** a disposition target; never `update_thread status=closed` on 480 |

**Preflight (advisory):** `session_close_preflight` may return
`bus_thread_disposition_warning` when `entity_ids` cites a still-open bus
thread — detection is **entity_ids-only** (no store-wide active scan).
`ok` stays true. When a work thread is omitted from `entity_ids`, this
section is the unconditional doc bind fallback.

## R12 completeness gate (life/web — friction 23944)

**Scope:** standing roots with a charter/brief deliverable sequence on **life MCP / claude.ai** (`agent_bus` + `fs(cortex)` + preferred `cortex://` packaging; `workspaces://` readable when exploration is named; ¬ code-only tools). Cursor coding orchestration (cursor-sdk dispatch, source_ref, repo recon) remains in `orchestrator-workflow`; this section is the **self-contained** reader/writer gate for web-anthropic.

1. **Scoreboard SOT.** One-page sidecar at `cortex://notes/system/threads/<thread-id>-charter-scoreboard.md` is the **sole completeness SoT** for the charter sequence (template: `charter-scoreboard.md`). Rows = deliverables; columns ≥ id/title · live `todo:`/`decision:` · status · evidence. CHECKPOINT **indexes** WIP and points here — ¬ substitute CHECKPOINT for completeness. Writer detail (birth, none-forbid, side-quest): Use `checkpoint-discipline`.

2. **Done / next / close-arc gate.** Before any claim the arc is done, nothing is next, or the root may close: `md_read` the scoreboard (or charter deliverable section if missing — then mint/update scoreboard) and diff against live Cortex cards. `¬` treat CHECKPOINT, empty Next-pickup, or chat WIP as completion authority. Unverified friction/deferred claims are not actionable until re-verified.

3. **Resume / checkpoint vocabulary.** Use the `checkpoint-discipline` skill (tip recipe, profile operator-facing, tip hygiene). This section does **not** own resume step 0.

The CHECKPOINT RESUME footer self-bootstraps via `checkpoint-discipline` (+ this § R12 for done/close); see that skill for the canonical string.

## Body size — Cortex sidecar pattern

Turn bodies are short briefings (target <2KB). Long task specs, analysis, substrates, handoffs, packets, reviews, or result memos go in a Cortex sidecar.

**Preferred (E4):** pass `sidecar_content` on `send` — the server writes the durable file after the thread id is known and before the turn row is inserted, appends a trailing `Sidecar: cortex://…` pointer to the body, and returns `sidecar_uri` + `sidecar_sha256`. Optional `sidecar_slug` overrides the default `slugify(subject)` filename.

```python
agent_bus(tool="send", arguments='{"new_slug":"topic","to":"grok-web","subject":"Task spec","body":"Full spec in sidecar.","sidecar_content":"# Task\\n...","from_agent":"web-anthropic"}')
```

**Manual fallback** (when send sidecar params are unavailable):

```python
fs(sandbox="cortex", op="write", path="notes/system/threads/<thread-or-slug>-<subject>.md", content="...")
agent_bus(tool="send", arguments='{"new_slug":"topic","to":"grok-web","subject":"Task: ...","body":"Full spec: cortex:notes/system/threads/<slug>-<subject>.md","from_agent":"web-anthropic"}')
```

The sidecar is the communication; the turn body is the table-of-contents entry. Use `allow_long_body=true` only when inline long-form is required by recipient contract.

Workspace mirrors (`tmp/reviews/...`) are secondary. For bus communication, cite the Cortex sidecar first.

**Life/web handoff packets:** `/mcp/life` cannot read `workspaces://` (`todo:life-fs-workspaces-unbound` Option 2). A `packet_path` under `tmp/reviews/` is for Stargate/cursor resolution only — before operator push, mirror the packet (and any checkout evidence the life seat must read) to cortex and cite the cortex URI. Detail: `handoff-packet-authoring` § Life-surface cortex-mirror gate. Friction a23964.

## Life→code lane tags

Capability-gap escalate from life → code: tag `lane:life-to-code` (+ `type:feature-request` when durable work suspected). Protocol + disposition: `life-to-code-request-lane` (¬ duplicate here).

Code-seat boot/triage probe for open requests:

```python
agent_bus(tool="threads", arguments='{"tags":["lane:life-to-code"],"status":"active"}')
```

## Lifecycle and read state

Close completed exchanges with `close(thread, summary)`; closed threads reduce false unread boot signals. Pass `mark_read:true` when fetching turns you intend to act on.

### Bulk inbox triage (`triage`)

For historical unread backlogs, use the two-phase `triage` op (**agent_bus only** — not on `agent_bus_read`):

```python
# 1. Preview (default dry_run=true)
agent_bus(tool="triage", arguments='{"from_agent":"cursor","older_than":"30d","action":"mark_read"}')
# → candidates, total_candidates, capped, confirm_token, expires_at

# 2. Execute the exact previewed set within 10 minutes
agent_bus(tool="triage", arguments='{"from_agent":"cursor","older_than":"30d","action":"mark_read","dry_run":false,"confirm_token":"<token>"}')
```

Guardrails:

- `older_than` is **required** (no default). Floors: `mark_read` ≥ 24h; `close` ≥ 7d (422 below floor).
- Hard cap **50 threads/call**; response includes `capped: true` when more qualify.
- Only threads where **every** unread turn is addressed to you (alias-resolved); `to:all`, other-recipient unread, `blocked`, and `pending`/`admitted` lifecycle threads are excluded.
- `close` is irreversible (marks all read + closes). First production backlog run: review dry_run output before confirm.

**F6a/F6b observability hooks** (event service):

| Signal | When | Min payload |
|---|---|---|
| `mcp.agentbus.triage.dry_run` | preview | `agent`, `filter`, `total_candidates`, `capped`, `confirm_token_id` |
| `mcp.agentbus.triage.executed` | mutate | `agent`, `action`, `thread_count`, `confirm_token_id`, `marked_read`/`closed` |

F6a (safety): measure reply/reopen-within-7d on triage-closed threads from lifecycle events + bus turns; breach >2% ⇒ raise floors or drop `close`. F6b (sunset): if steady-state unread <30 and triage fires <1×/month post-cleanup, retire `close` at +90d review.

## Related

- `checkpoint-discipline` — CHECKPOINT author/resume/tip hygiene/profiles (standing roots).
- `agent_bus` descriptor — send/reply/fetch/wait signatures.
- Session-close discipline — bus debrief before closing.
