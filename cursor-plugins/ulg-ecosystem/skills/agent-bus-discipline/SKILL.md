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

## Backward read (established threads)

Whole-thread fetch of large threads hangs. (1) `get(turn_number="latest")`. (2) `fetch(compact=true, last=…)` in ~20–30 turn windows backward. (3) Scan subjects for LANDED, VERDICT, RULING, CHECKPOINT, closeout. (4) `get` matching turns newest-first. (5) Stop when answered.

## Body size — Cortex sidecar pattern

Turn bodies are short briefings (target <2KB). Long specs go in a Cortex sidecar.

**Preferred (E4):** `sidecar_content` on `send` — server writes durable file, appends `Sidecar: cortex://…`, returns `sidecar_uri` + `sidecar_sha256`.

```python
agent_bus(tool="send", arguments='{"new_slug":"topic","to":"grok-web","subject":"Task spec","body":"Full spec in sidecar.","sidecar_content":"# Task\n...","from_agent":"web-anthropic"}')
```

**Manual fallback:** `fs` write under `notes/system/threads/…` then `send` with pointer in body. Use `allow_long_body=true` only when recipient contract requires inline long-form.

## Related

- `universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/agent-bus-discipline/reference.md` — standing roots, thread genus, lifecycle, CONFER, Gate 1, lane parentage, R12, session-close; § **Dispatch polling (historical — other seats)**.
- `universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/reference.md` § **How this seat works** / § **Standing authority** — Castle maestro polling removed, liveness, watcher recovery.
- `checkpoint-discipline` — CHECKPOINT profiles and resume.
- Session-close discipline — bus debrief before closing.

