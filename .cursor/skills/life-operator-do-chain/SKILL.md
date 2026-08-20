---
name: life-operator-do-chain
description: "When the operator says have cursor do X without naming a hop — run remaining hops; do not quiz. Named Architect / Mission Architect / Conductor are first-class and pin that hop only."
trigger_short: "have cursor do ∨ just do it ∨ Architect ∨ Mission Architect ∨ Conductor"
skill_category: orchestration
trigger_match_terms:
  - life-operator-do-chain
  - have cursor do
  - just do it
  - make it real
  - make this real
  - mission architect
  - mission writer
  - conductor-architect
  - Architect
  - Conductor
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

Two registers, both legal. English outcome ⇒ seat composes the chain. Named hop ⇒ that hop only. Do not quiz. Do not hide hop names in chat.

```
have_cursor_do(X) ∧ ¬named_hop ⇒ run(remaining(X))
named_hop ⇒ that hop only
¬ quiz(Architect | Mission Architect | Conductor)
¬ flatten(hop_names) in operator chat
```

Canonical hops (say these; aliases in parentheses):

| Hop | Does | Alias |
|---|---|---|
| **Architect** | Bind the plan / shape (Fable/CDP consult) | “architect a plan”, “dispatch to Architect” |
| **Mission Architect** | Author the conductor packet + scoreboard so the mission is runnable | Mission writer, conductor-architect |
| **Conductor** | Admit and operate the mission root | “admit the conductor”, “run it” |

Lawful sequence: Architect → Mission Architect → Conductor. They may name any one step.

## Remaining hops

Skip anything already harvested (consult sidecar, conductor packet + scoreboard, G-row DONE).

| They say | Seat runs |
|---|---|
| “architect a plan” / “dispatch to Architect” / “think / what should this be” | **Architect** only |
| “dispatch to Mission Architect” / “architect the mission” / “write the mission” | **Mission Architect** only |
| “dispatch to the Conductor” / “run it / ship it / admit the conductor” | **Conductor** only |
| “have cursor do X” / “just do it” / “make X real” (no hop named) | **remaining** hops, no quiz |
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
4. Announce each hop by its canonical name (Architect / Mission Architect / Conductor) + seat + why. Do not ask which hop. Do not paraphrase hops as only “remaining work.”

Specimen: 9473 Architect harvested ∧ 9487 Mission Architect harvested ∧ 9488 packet+scoreboard exist ⇒ “have cursor do recall” admits the **Conductor** on 9488 — ¬ re-ask Architect / Mission Architect.

## Anti-patterns

| Bad | Good |
|---|---|
| “Do you want Architect, Mission Architect, or Conductor?” | Run remaining, or fire the hop they named |
| Hide hop names in chat (“the next remaining hop”) when they are speaking hops | Say Architect / Mission Architect / Conductor |
| “have cursor do X” → only `request` / `delegate` | Compose the chain |
| Re-run a harvested hop because they did not name the next | Skip harvested |
| Quiz after they pinned one hop | That hop only |
| Title / WIP `{monitor} dispatched elsewhere` | Name root + hop + worker (`9488` Conductor on `9493`) |
| Fire remaining hops **on** the monitor thread | Fire on the mission root; monitor stays silent on the watched consult |
