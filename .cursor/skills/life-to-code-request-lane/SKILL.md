---
name: life-to-code-request-lane
description: "On life→code capability-gap escalation — web-anthropic posts agent_bus lane:life-to-code; code seat dispositions direct-first | todo-minted | declined. No new life-intent verb; async only."
related_skills: ["consult-routing", "agent-bus-discipline", "entity-lifecycle-discipline"]
---

# Life→Code Request Lane

Interim sibling to F4 (`todo:life-cursor-sdk`). Life seats escalate capability gaps to the code seat via **teach + agent_bus** — no new life-intent verb, no synchronous `team_dispatch` from life.

**Parents:** `decision:seat-lane-split-liaison-model` · densify `agent-bus:5145` · `todo:life-to-cursor-request`.
**Pointers:** `consult-routing` § Surface gate option 2 · `agent-bus-discipline` § Life→code lane tags.

## When (life)

Gap-shaped / unsettled: life tools cannot achieve the outcome → this lane.
Verb-shaped + settled work order → `life-intent` propose (existing code-seat-scout under investigate) — not this lane.
Try life surface / one skill read first (K-L1).

## Request shape (life → `to=cursor`)

| Field | Rule |
|---|---|
| Gap | What life tools cannot do |
| Attempt evidence | What was tried, or why none was needed |
| Desired **outcome** | Result to verify — ¬ mechanism / ¬ patch recipe |
| Context | Prefer `cortex://` / entity / `agent-bus:`; `workspaces://` readable when exploration is named |
| Prescription | **Forbidden** — code seat re-derives (K-L2) |

```python
agent_bus(tool="send", arguments='{"new_slug":"life-gap-<short>","to":"cursor","from_agent":"web-anthropic","subject":"Life→code: <outcome>","tags":["lane:life-to-code"],"body":"Gap: …\\nAttempt: …\\nOutcome: …\\nContext: cortex://…"}')
```

Add `type:feature-request` when durable product change is already suspected.

## Async honesty

Delivery = **next code-seat session** or **operator prompt**. Life cannot fire CODE_EXTRA. ¬ pretend synchronous escalation.

## Disposition (code seat — exhaustive)

On pickup of an open `lane:life-to-code` thread:

1. **resolved-direct** — iff bounded (one session, no design fork, no new invariant/config surface). Verify outcome with evidence; reply on the lane thread.
2. **todo-minted** — iff ANY of: (a) durable product change (tool/op/descriptor/skill/registry/config); (b) same gap class ≥2; (c) direct attempt exceeds bound or opens a fork. Reply with `todo:` id; lane thread = provenance. Mint ≠ implement authorization (Gate-2 / skeptic unchanged).
3. **declined** — else, with reason on the thread.

Close every lane thread as exactly one of those three. Web owns gap+outcome; code owns disposition.

## Boot / triage discoverability (code)

```python
agent_bus(tool="threads", arguments='{"tags":["lane:life-to-code"],"status":"active"}')
```

Run at code-seat boot/triage when scanning for open escalate requests. Existing `threads(tags=…)` — no parallel router.

## Anti-patterns (falsifiers)

| ID | Fail | Response |
|---|---|---|
| K-L1 | Always-bus (≥30% of window achievable with life tools) | Tighten try-life-first |
| K-L2 | Recipe leak (code executes web prescription verbatim) | Reject shape; re-derive |
| K-L3 | Code-work laundering (minted todos skip Gate-2/skeptic) | Route through lifecycle |

Also: todo-mint-as-deflection (mint to dodge a bounded direct fix).

## Non-goals

F4 life-sdk build · new life-intent verb · dual-endpoint / imprint · event vocab for lane v0 (bus tags suffice).
