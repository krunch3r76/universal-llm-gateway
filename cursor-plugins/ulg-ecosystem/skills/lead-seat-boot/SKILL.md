---
name: lead-seat-boot
description: "Lead seat boot — cortex_brief hard-stop, agent_bus project sweep, live-tool trust, cross-session continuity for web-anthropic and cursor lead."
---

# Lead Seat Boot Protocol

Applies to `web-anthropic` and `cursor` as lead. Supersedes retired `claude-web-boot` (historical).

Authority: agent-bus thread 1082; decisions 11054/11055; gates 3–4 per thread 1084.

## Surface gate (life vs code)

Life MCP excludes CODE_EXTRA (`team_dispatch`, `panel_dispatch`, `pipeline`,
`manage`, `observability`). Boot cognitive gates in this skill run on every seat.
CODE_EXTRA call sites = **code MCP only**. On life/claude.ai: (1) run cognitive
legs in-seat; (2) `agent_bus` ask a code seat to fire transport or infra ops; or
(3) skip CODE_EXTRA-dependent steps — ¬ call CODE_EXTRA from life.

## Gate 1 — `cortex_brief` (required; hard-stop on failure)

Call `cortex_brief` with seat-appropriate args (Seat Annex). On error/unreachable: halt and surface error verbatim.

Prohibited fallback: individual `entity_get`/`search`, or assuming prior context is current.

On success: capture server-minted `session_id` before reading/evaluating/distrusting briefing content. Pass it to `edge_create`, `supersede`, and `session_close` for the session. ¬ defer until close; ¬ use placeholders in handoff chains.

Continue Mode exception: explicit transcript ID in opening message satisfies boot intent. Note missing brief. If deferred `cortex_brief` runs later, apply capture-first then.

Internalize: Arc digest, todos, prior context, active-thread summary, review queue, deadlines. Operator posture binds from boot card + Use the `operator-posture` skill.

## Skill body delivery

**Invariant:** `¬ fs_read(skill_body → context)`. Use skills by canonical slug / `agent_skill:{slug}` only (`Use the `{slug}` skill`). Bodies arrive via seat self-fetch (web-anthropic Customize→Skills; Cursor `<available_skills>`), server inject (dispatch CODING / Layer-C), or MCP body API — never via agent-side `fs` of skill markdown.

| Seat class | Body delivery |
|---|---|
| Platform seats (`web-anthropic`, Anthropic app, Cursor IDE) | Native skill layer: description-gated stubs → full body on explicit use. Do NOT `fs md_read` skill bodies. |
| API dispatch roles | Server inject via dispatch packet `<invariants>` / generate CODING scope |
| `cursor-sdk` | Packet `Use the `{slug}` skill` lines + server materialize |

**Authoring-only exception (not runtime load):** skill editors write SOT at `workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md` via `fs(op="write"|"replace")` — see `skill-document-writing`. Do not confuse authoring paths with consumption.

Boot cards and orientation blocks NAME required-gate skills by canonical slugs — `Use the `{slug}` skill` fires seat self-fetch; ¬ agent-side skill-path fs (friction 23128 / agent-bus:4888).

## Gate 2 — agent_bus thread sweep (required for project-entity work)

Trigger: session involves a todo entity tagged to a project.

```python
agent_bus(tool="threads", arguments='{"project_tag":"<active-project>","status":"active"}')
```

`unread_count>0 ∧ (last_turn_to includes active seat ∨ topic intersects task) ∧ turn_age<4h ⇒ fetch/read before first infra-touching call`.

Threads >24h old with no topic intersection may be deferred.

**Gate 2b — attended lifecycle commission (BINDING — `cdp-operator-proxy` inv 40).** On the **code/IDE cursor** seat only: unread `to=cursor` whose subject/body names `manage` / `sync_restart` / GIW recycle / code-seat lifecycle ⇒ fetch and **execute** in this session. `COME TO IDE` means this seat is now live. ¬ wait for the human to restate the restart. Life seats `send` that commission; they do not execute it.

Gate 2 must precede: `manage(...)` (code surface only), `fs` writes to service config paths, and `observability` (code surface only) for live debugging.

## Gate 3 — active-todo assertion sweep (required before drafting on todo)

Trigger: implementing/modifying work tracked by `todo:*`.

```python
cortex(tool="entity_get", arguments='{"entity_id":"todo:<slug>","intent":"full"}')
```

Read all assertions, not just description. Focus on confirmed user statements, recent supersede chains, and declined alternatives.

`implementation_design contradicts confirmed user_statement assertion ⇒ supersede_assertion_before_drafting`.

Description may be stale; assertion log is canonical. Anti-pattern: recording divergence in local `plan.md` and treating it as approval.

## Gate 4 — schema changes via migrations only

Any SQLite DDL (`CREATE INDEX/TABLE/TRIGGER`, `ALTER TABLE`, `DROP INDEX`, etc.) against Cortex, RAG, events, or agent-bus lands via `libs/<store>/migrations/NNN_*.sql|py`.

| Forbidden | Required |
|---|---|
| ad-hoc `python -c` DDL against live DB | migration file with `IF NOT EXISTS` where applicable |
| `sqlite3 <db> "ALTER…"` against live state | verification against `:memory:` or tmp-copy DB |
| “verification/smoke” scripts mutating live state | `manage(sync_restart, cortex_api)` (code surface only) to apply runner migrations |

System “avoid creating files” rule does not apply to migration files. EXPLAIN/PRAGMA verification must use `:memory:` or tmp copy. Backup before live one-liner does not make it safe.

## Gate 5 — bus subject/body intent alignment

Trigger: every agent_bus post/reply that could direct destructive action (force-remove, delete, drop, force-push, discard).

`∀ bus_message: subject.intent = body.intent`.

Update stale `Re:` subjects when body intent shifts. Pre-post check: read subject and first body sentence together; if they imply opposite actions, fix one.

Failure anchor: subject implying merge/keep + body directing discard/force-remove of in-flight work.

## Live Tool > Repo Artifacts

`live_tool_response(manage|observability|event_service)` (code surface only) conflicts repo_artifact ⇒ trust_live_tool. Repo may contain deploy options inactive on host.

## Cross-session continuity

Before new ticket/trace: `agent_bus(threads, scoped to topic)` for open threads.

Before infra fix: `cortex(search, query=<surface>)` for existing `decision:*`; reference existing decisions, do not open parallel ones.

## Seat Annex

| Seat | cortex_brief call | MCP surface notes | Boot doc |
|---|---|---|---|
| `web-anthropic` | `cortex_brief(agent="web-anthropic", family="claude", platform="web", role="lead")` | Full vortex except `manage` / cursor IDE tools. Dispatch local work → `fs`; peer consult → `team_dispatch(op=generate, role=reviewer|…)` (code surface only — on life: `agent_bus` a code seat); never `role=web-anthropic` self-spawn. Read `dispatch-workflow.md` §0a before first dispatch. | This skill |
| `cursor` | `/cortex-brief` command | Full vortex + cursor IDE tools | `cursor-boot_ws.mdc` |

## Cursor dispatch packet compliance

When cursor dispatches work to a lead seat, packet must include:

| Field | Required | Purpose |
|---|---|---|
| `active_project_tag` | yes | scopes Gate 2 sweep |
| `cortex_brief_confirmed` | yes | avoids double-brief overhead |
| `related_thread_ids` | when applicable | open threads intersecting task |

`project_entity_work ∧ missing(active_project_tag) ⇒ receiving_seat requests it before proceeding past Gate 2`.
