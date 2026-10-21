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

Polling is removed on the **Castle maestro operator seat**. Do not call `agent_bus` `wait` or `poll_hint` loops from the Castle maestro operator seat. Milestones and exceptions go `to=maestro` per L17 allowlist. `predicate_unmet` is not a reason to re-poll on that seat. Watcher and liveness: `universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/reference.md` § **How this seat works**.

## Pre-flight before reply

Use `get(thread, turn_number="latest")` when you have not fetched this thread in-session. On `409 unread_turns_exist`, read `latest_turn_number` from the error body and retry with that value as `after_turn`.

`deliverable | GO | pointer | CHECKPOINT` on thread A while dispatch/consult thread B governs the work ⇒ `fetch_unread(thread=B, mark_read=true)` immediately before send.

`409` ⇒ stale `after_turn` or unread turns; use `detail.latest_turn_number`. `transport_or_connector_error` without HTTP ⇒ retry once, then escalate.

## Large-payload navigation

If `fetch(last=1, compact=true)` overflows, use `get(turn_number="latest")` or `get(turn_number=N)` per turn.

## Backward read (established threads)

Whole-thread fetch hangs on large threads: `get(latest)` → `fetch(compact=true, last=…)` in ~20–30 turn windows → scan subjects (LANDED, VERDICT, RULING, CHECKPOINT, closeout) → `get` hits newest-first → stop when answered.

## Body size — Cortex sidecar pattern

Turn bodies are short briefings (target <2KB). Long specs go in a Cortex sidecar.

**L29b (L17 waking milestones to `maestro` / `web-anthropic`):** heads `PLAN`, `VERDICT`, `LANDED`, `DONE`, `FAILED`, `BLOCKED`, `QUESTION`, `RULING`, `ACK`, `AMEND`, `SEAT_REGISTRATION` — inline `body` **≤ 2,000** chars unless `sidecar_content` or `allow_long_body=true`; else HTTP **422** `milestone_body_over_2000_chars`. Detail: reference § **Maestro milestone body cap (L29b)**.

**Preferred (E4):** `sidecar_content` on `send` — server writes durable file, appends `Sidecar: cortex://…`, returns `sidecar_uri` + `sidecar_sha256`.

```python
agent_bus(tool="send", arguments='{"new_slug":"topic","to":"grok-web","subject":"Task spec","body":"Full spec in sidecar.","sidecar_content":"# Task\n...","from_agent":"web-anthropic"}')
```

**Manual fallback:** `fs` write under `notes/system/threads/…` then `send` with pointer in body. Use `allow_long_body=true` only when recipient contract requires inline long-form.

## Related

- `agent-bus-discipline/reference.md` — standing roots, genus, lifecycle, CONFER, Gate 1, R12, session-close; § **Maestro milestone body cap (L29b)**; § **Dispatch polling (historical — other seats)**.
- `cdp-operator-proxy/reference.md` § **How this seat works** — Castle maestro polling removed, liveness.
- `checkpoint-discipline` — CHECKPOINT profiles and resume.

