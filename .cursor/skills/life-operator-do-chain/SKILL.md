---
name: life-operator-do-chain
description: "When a life operator says have cursor do X, just do it, or make it real without naming Architect, Mission writer, or Conductor — run remaining hops; do not quiz."
trigger_short: "have cursor do ∨ just do it ∨ make it real ∨ life operator verbs"
skill_category: orchestration
trigger_match_terms:
  - life-operator-do-chain
  - have cursor do
  - just do it
  - make it real
  - make this real
  - mission writer
  - conductor-architect
  - Architect
  - life operator verbs
related_skills:
  - consult-routing
  - conductor
  - work-item-seed-path
  - agent-bus-discipline
  - lean-context-dispatch-first
---

# Life operator do-chain

SOT: `decision:life-operator-do-chain`.

Operator names the **outcome**. Seat picks hops. They do not owe Architect / Mission writer / Conductor.

```
have_cursor_do(X) ⇒ run(remaining(X))
¬ quiz(Architect | Mission writer | Conductor)
named_hop ⇒ that hop only
```

## Remaining hops

Skip anything already harvested (consult sidecar, conductor packet + scoreboard, G-row DONE).

| They say | Seat runs |
|---|---|
| “think / what should this be / architect X” | **Architect** — Fable/CDP consult only |
| “write the mission / make it runnable” | **Mission writer** — conductor-architect packet + scoreboard only |
| “run it / ship it / admit the conductor” | **Conductor** only |
| “have cursor do X” / “just do it” / “make X real” | **remaining** hops, no quiz |
| “watch / don’t post / just look” | monitor — not this chain |
| a MONITOR CHECKPOINT next-pickup (“admit the conductor”) | remaining hops on the **mission root** — ¬ commission the monitor thread |
| “remember this / write that down” | `imprint` |
| “what do we know / where did we leave off” | `recall` when shipped; else remaining hops that ship it |

`request` / `delegate` are how a hop is armed — not the meaning of “have cursor do X”.

`dispatched` names a **hire** (`request` / `team_dispatch` generate). A thread id is not a seat. Do not say `{monitor} dispatched elsewhere` — name the root, the hop, and the worker. A monitor next-pickup is a cue, not a dispatch of the monitor. When an executor chat takes that cue, the monitor seat **ends**; CHECKPOINT the monitor (pickup stale → root pointer). The executor is no longer MONITOR.

## Procedure

1. Pin X (outcome). Name harvested hops + evidence (thread, sidecar sha, `execution_id`).
2. If they named one hop → fire only that hop.
3. Else fire the next unharvested hop; continue until remaining is empty or a true operator-only gate.
4. Announce each hop in one line (seat + why). Do not ask which seat.

Specimen: 9473 Architect harvested ∧ 9487 mission writer harvested ∧ 9488 packet+scoreboard exist ⇒ “have cursor do recall” admits the conductor on 9488 — ¬ re-ask Architect / Mission writer.

## Anti-patterns

| Bad | Good |
|---|---|
| “Do you want Architect, Mission writer, or Conductor?” | Run remaining |
| “have cursor do X” → only `request` / `delegate` | Compose the chain |
| Re-run a harvested hop because they did not name the next | Skip harvested |
| Quiz after they pinned one hop | That hop only |
| Title / WIP `{monitor} dispatched elsewhere` | Name root + hop + worker (`9488` conductor on `9493`) |
| Fire remaining hops **on** the monitor thread | Fire on the mission root; monitor stays silent on the watched consult |
