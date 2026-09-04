---
name: agent-bus-discipline
description: "Mechanics for agent_bus: new-thread vs reply, sidecars, lifecycle, thread genus (productive|custodial). have cursor do X → remaining hops (life-operator-do-chain); do not quiz."
trigger_short: "agent_bus thread ∨ reply ∨ sidecar ∨ thread genus ∨ have cursor do"
skill_category: dispatch-delegation
trigger_match_terms: ["agent-bus-discipline", "agent_bus_discipline", "agent_bus", "thread", "reply", "sidecar", "dispatch-delegation", "mechanics", "operations", "new-thread", "productive", "custodial", "thread-genus", "non-work", "have cursor do", "do-chain"]
---

# Agent Bus Discipline

**Authority:** any seat that calls `agent_bus` against the ULG hub or a
ULG-ecosystem satellite — Cursor IDE, CDP operator-proxy, and life surfaces
(¬ hub-only / ¬ workspace-specific). Spine / genus / species apply at **lane
birth and pivot**, not only at standing-root CHECKPOINT.

## New thread vs reply

Default to a new thread unless the message is a direct continuation of an existing exchange.

| Situation | Op |
|---|---|
| New task, even same topic | `send(new_slug=...)` |
| Direct reply to a specific turn, same task/live exchange | `send(thread=...)` |
| Follow-up after prior agent responded | `send(thread=...)` |

Topic overlap ≠ thread continuity. Prior thread can be evidence context; new task still gets new thread. `from_agent` is required. Legacy `post`/`reply` are deprecated; prefer `send` where available.

## Dispatch polling

After `team_dispatch` / handoff with `poll_hint`, poll using `agent_bus(wait, thread=N, after_turn=T, completion=<from poll_hint>, from_agent=...)`. CDP handoffs ship `completion="proof_reply_from"` — do not downgrade to `first_reply_from`; chrome-only CDP envelope turns yield `predicate_unmet`, not `complete`.

```python
agent_bus(tool="wait", arguments=poll_hint.arguments_json)
```

`wait` is one server-side short-block call; re-call to keep polling. `wait_seconds ≤ 60`
(0 = snapshot). Prefer `poll_hint.arguments_json` — Cursor-IDE seats get
`wait_seconds=0`; web/API keep 60 (friction 24081; life MCP client ceiling). Attended spinner >2 min on
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

State reconstitution default = tip CHECKPOINT + roadmap (+ scoreboard when chartered). Completeness projection = scoreboard (graph canonical). Window history = `## Windows` on the charter surface (schema §3.5) — resume does not load it. `empty(Next-pickup) ⇏ arc_complete`. **Done-claim gate:** § R12. Templates: `charter-scoreboard.md` · `continuity-doc.md`.

### Orchestration birth gate (binding — operator 2026-07-30)

```
∀ spine=root (orchestration thread): charter_exists ∧ objective_bound
∀ orchestrator_continuity: stance := Use(ulg-for-llms) ∧ Why_this_house
```

**Charter** here is distinct from **`charter-runner` enrollment** (machine tick). Manual orchestration (`orchestrator_continuity`) still requires a charter; enrollment only selects the CHECKPOINT profile (`tick_charter` vs `orchestrator_continuity`).

| Surface | Required when | Minimum |
|---|---|---|
| **Objective** | always on birth; reprint on every continuity resume as **`Mission:`** + In/Out | one bound sentence (`Objective:`, `## Anchor`, or `Primary OPEN:`) — `decision:continuity-resume-mission-open` |
| **Scoreboard** | deliverable sequence ∨ `charter-runner` enrolled | `cortex://…/<thread-id>-charter-scoreboard.md` indexed in CHECKPOINT |
| **Continuity doc** | manual root without scoreboard yet | `continuity-doc` or `Charter: cortex://…` pointer + objective |
| **Stance** | `orchestrator_continuity` (unenrolled) | `## Stance`: Use the `ulg-for-llms` skill + `## Why this house` (durable on the continuity-doc; birth CP indexes). `tick_charter` skips |

**Birth order (do not invert):** (1) mint charter surfaces (scoreboard and/or continuity doc) **with stance** (`## Stance` + `## Why this house`) → (2) post birth CHECKPOINT indexing them + `## Stance` + concrete Next-pickup → (3) stamp `role:root` / enroll when belt path applies.

**Close / disposition forbids on newborn roots:**

| Situation | Disposition | ¬ |
|---|---|---|
| Just established orchestration | `leave-open+reason` (charter birth in flight) | `advise-close` |
| Empty / ungated Next-pickup on birth | incomplete birth — fix charter surfaces | arc complete |
| Session close with open root | report disposition fields | treat as permission to declare done |

Server posts a non-blocking `briefing_advisory` on birth/bootstrap CHECKPOINTs: `reason=root_missing_charter` when charter surfaces are missing; `reason=root_missing_stance` (`turn_kind=continuity_stance`) when an unenrolled continuity root lacks Use `ulg-for-llms` or a Why this house pointer. Fix before relying on the root.

Doctrine: `orchestrator-core` § Standing root threads · `orchestrator-workflow` R12.

Session close for active root must include root thread id, roadmap path, latest checkpoint or stale flag, active item ids/seats, child root ids, and scoreboard path when applicable. Root required for multi-session, multi-seat, or multi-wave arcs; exempt one-pass work with no durable sequencing.

**Endeavor dual-surface pattern (binding — operator 2026-07-15, agent-bus:5129).** Life/web work uses **exactly two** bus surfaces — do not mint a third “letter vs corpus” split:

| Surface | Who | What |
|---|---|---|
| **Endeavor root** | cursor + operator | CHECKPOINT, scoreboard pointers, **Child lanes** / **Cited lanes** (projected `parent_thread` + `lane_role`), closeout summaries. Completeness **projection** = scoreboard (graph canonical). ¬ web rewrite traffic. |
| **One life side channel** | web-anthropic (+ cursor packets) | Everything web must **act** on for this endeavor: interrogate, rewrite, nit binds, lock claims; corpus admits/harvest when web is the actor. Samples/INDEX are **file context** (pseudo-RAG), not a second bus thread. |

Any new `agent_bus_store` route that persists a turn body must route through `prepare_body_for_insert()` / `build_turn_created()` in `body_auto_spill.py` — those are the sole funnel points for CHECKPOINT projection and root auto-stamping. Do not call `insert_turn`/`create_turn` directly with an unprocessed body.

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

### Genus (prose, not a classification axis)

SOT: `decision:thread-genus`. When the operator points at a thread or you are about to name one, **apply the labels** — do not quiz. They do not owe you these words.

Operator English → you translate (do not correct their wording):

| They say | You treat it as |
|---|---|
| “watch that / don’t post / just look” | custodial · monitor |
| “the main arc / standing root / keep this open” | orchestration thread (`role:root`) |
| “do the work here / implement on this / this is the job” | productive (this thread) |
| “have cursor do X / just do it / make it real” | remaining hops — Use the `life-operator-do-chain` skill; ¬ quiz. Named Sketch / Mission Composer / Conductor pin that hop |
| “what is this thread?” | you label it (spine + genus + species if useful) |
| “don’t mix the watch with the work” | keep 9471-class and 9482-class apart |

Then stack:

1. **Spine:** orchestration thread (`role:root`) vs work thread (default).
2. **Genus:** **productive** (advances a deliverable, decision, or answer) vs **custodial** (watches, preserves, or reconstitutes). Never say “work thread” to mean genus-work.
3. **Species** only if useful: productive → implement / consult / request / debrief; custodial → monitor / continuity sibling.

Specimens: **9471** = orchestration + productive + implement; **9482** = work-spine + custodial + monitor. **9483** = work-spine + custodial + monitor (`watches:9473`). **9488** = orchestration + productive (recall mission root). This IDE chat executing 9483’s next-pickup is **not** “9483 dispatched” — it is remaining hops on **9488**.

`dispatched` = hire a seat. `¬ dispatch(thread_id)`. `elsewhere` is not a location — name root + worker + seat.

Do not mint `genus:` tags until that decision's promotion trigger fires.

**Three registers (BINDING — Fable 9518 / `decision:thread-genus`):** do not
read `slug` or `type:*` as the current contract. They are birth graffiti.

| Register | What it is | When it changes |
|---|---|---|
| **Tags** (`type:*`, `lane:*`, `bus_lifecycle:*`) | Birth graffiti. Optional `type:` is a facet hint, not current work. Never mint `genus:` tags. | Birth (and rare operator retag). A pivot does **not** rewrite tags. |
| **Summary** | Refreshed so-what. `resolve_so_what_summary` runs on every `request`. | Every `request` / hop. This is the current-contract surface. |
| **Standing handoff** | Full state (WIP, next, blockers). | When a CHECKPOINT or hop needs reconstitution. |

## Session-close thread disposition

**When:** substantive close ∧ session used `agent_bus`.

**Shared enum (all branches):** `advise-close` | `closed` | `leave-open+reason`

Disposition derives from **spine** (+ 480 special) — not a parallel taxonomy:

| Spine / special | Close report block |
|---|---|
| **root** (standing continuity) | At minimum `advise-close` + root thread id, roadmap path, latest checkpoint turn or stale flag, active item ids/seats, child root ids, scoreboard path when chartered — **unless** orchestration was just birthed and charter surfaces are still incomplete ⇒ `leave-open+reason` (see § Orchestration birth gate); `advise-close` is a disposition report, not arc-complete authority. After `session_close` 201, fill `## Windows` Arc + `journal_row_id` on the charter surface (schema §3.5) |
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

Liaison seat procedure compose: `runbook:liaison-seat-on-a-lane` § Procedure step 4.

## Lane parentage (operator-visible)

Folded lane parentage lives on **`thread_get`**, **unread TOC**, and CHECKPOINT derived zones as `parent_thread` + `lane_role` (store authority: append-only `thread_lane_associations`). Bind explicitly via `lane_bind` or atomically on `send(new_slug=…)` when both fields are supplied. Author prose may use `agent-bus:7188 (sub_mission of 7182)`; role-less bare citations get advisory lint only (non-blocking). CHECKPOINT derived zones split **Child lanes** (depth-1 substantiated) vs **Cited lanes** (citation-derived / deeper). A conductor worker minted under a coord stub is a leftover cite (or `lane_bind` onto the root), not a Child lane of the root — 422 `conductor_coord_split_refused` blocks the class going forward.

Workspace mirrors (`tmp/reviews/...`) are secondary. For bus communication, cite the Cortex sidecar first.

**Life/web handoff packets:** `/mcp/life` cannot read `workspaces://` (`todo:life-fs-workspaces-unbound` Option 2). A `packet_path` under `tmp/reviews/` is for Stargate/cursor resolution only — before operator push, mirror the packet (and any checkout evidence the life seat must read) to cortex and cite the cortex URI. Detail: `handoff-packet-authoring` § Life-surface cortex-mirror gate. Friction a23964.

## Life→code lane tags

Capability-gap escalate from life → code: tag `lane:life-to-code` (+ `type:feature-request` when durable work suspected). Protocol + disposition: `life-to-code-request-lane` (¬ duplicate here).

**Sibling probe (cursor-auto farm):** find 9496-class siblings with
`lane:cursor-auto`. `lane:life-to-code` is a narrower overlay, not the farm census.

```python
agent_bus(tool="threads", arguments='{"tags":["lane:cursor-auto"],"status":"active"}')
```

Code-seat boot/triage probe for life→code hops:

```python
agent_bus(tool="threads", arguments='{"tags":["lane:life-to-code"],"status":"active"}')
```

## Lifecycle and read state

Close completed exchanges with `close(thread, summary)`; closed threads reduce false unread boot signals. Pass `mark_read:true` when fetching turns you intend to act on.

### cursor_request on a private lane — continue vs resume

Two legal states for `agent_bus.request(thread=…)` / `cursor_request(thread=…)` on the **same** private lane:

| State | Census | Identity bind | When |
|---|---|---|---|
| **continue-while-running** | N=1 active-work row | `origin_cse` or `single_seat_active_work` | Prior Auto job still `pending`/`running` on that thread |
| **resume-after-terminal** | N=0 after `terminal_done` | `watch_resume` → `mailbox_resume` → `cse_resume` → `origin_cse` | Prior job finished; watch / mailbox / bus CSE still names the holder |

Watch `registration_id` is lease SOT for hop **and** resume identity when `census_n==0`. It is **ignored** when `census_n==1` (continue path unchanged).

When **no** resume identity exists, admission returns `seat.identity_unresolvable` with `retryable:false` — that is a **pivot**, not a retry loop. Escape: `new_slug` + `parent_thread` + `lane_role=sub_mission` (child-thread fallback), not hammering the same `thread=` admission.

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

## Gate 1 audit leg (thread 191)

**Invariant (BINDING):** `post(Gate1_audit, thread=191)` on the same leg implies
`wake(web-anthropic) ∧ arm_watcher(191) ∧ transition_pager("audit in flight")`.
Post without wake + watcher on the **same leg** is incomplete — the operator never
says "wake" separately; movement is implied.

| Leg element | Requirement |
|---|---|
| Post | Structured audit to thread **191** (no diff inline) — files changed, what it does, risk controls touched, assumptions |
| Wake | CDP wake web-anthropic on the same leg (`team_dispatch(model=cdp/…)` or escape per `claude-ai-cdp-navigation`) |
| Watcher | Arm thread-191 consult watcher before leg close |
| Pager | Transition awareness page: audit in flight |

Mechanical entry: `scripts/post-gate1-audit.sh --subject … --body-file …`
(default `--watch-label watch-191`, `--thread 191`).

Doctrine: `decision:gate1-audit-implied-movement` · claudeburst `audit_ws.mdc` Step 1.

## Related

- `checkpoint-discipline` — CHECKPOINT author/resume/tip hygiene/profiles (standing roots).
- `agent_bus` descriptor — send/reply/fetch/wait signatures.
- Session-close discipline — bus debrief before closing.
