---
name: residual-imprint
description: "When leftovers/residuals are named — leftover, residual, deferred, defer, NOT-NOW, park, parking, out-of-charter, arc close, later wave, follow-up, sibling fork, writing hygiene — imprint on the substrate entity; association is memory."
skill_category: workflow
trigger_match_terms:
  - leftover
  - residual
  - deferred
  - defer
  - NOT-NOW
  - park
  - parking
  - out-of-charter
  - arc close
  - later wave
  - follow-up
  - sibling fork
  - writing hygiene
  - feature request
  - file this
---

# Residual Imprint

**Ultimate goal:** regular residual→entity imprinting so association/salience is memory — not a closeout checklist, not a one-arc habit.

**What-SOT:** `decision:leftover-entity-association-parking` — read for full posture; this skill teaches the pattern only.

## When to load

- A leftover/residual is **named** in work ("defer", "later", "NOT-NOW", "park", "follow-up", "out of charter/scope", "sibling fork", writing-hygiene leftovers)
- Operator names a **feature ask** ("feature request", "file this", "how do I refer this later?") that is not commissioned work
- Arc close / session close / checkpoint — sweep for named residuals still in chat or prose only
- Pre-close audit or checkpoint discipline crosslink fires

## General park-on-entities pattern

### 1. Pick the substrate entity

Park on the **matter/substrate the residual concerns** — the todo, decision, spec, friction, or artifact it attaches to.

- ¬ a detached "leftovers list" entity
- ¬ chat memory or closeout prose only
- If no entity exists yet, create one (`entity_create`) then park
- **Feature ask** (new capability, design open, not commissioned) → `friction(category=feature)` on the owner `service:`/`agent_skill:` — Use the `friction-review` skill § Classify before park. Optional edge to a related decision. `¬` `DEFERRED`-only on a decision (that row is invisible to `frictions`)

### 2. Assert vs edge

| Situation | Mechanic |
|---|---|
| Residual is a claim, deferral, or commitment about the entity | `cortex(tool="assert", …)` on that entity |
| Residual links two matters (sibling fork, cross-arc follow-up) | `relationship_create` or `edge_create` between entities |
| Both apply | assert on primary + edge to related matter |

### 3. Claim shape

- **Deferral:** `DEFERRED (reason/date): <what> — criterion: <when/how reopens>`
- **Follow-up:** `FOLLOW-UP: <action> — blocked on: <gate>`
- **Out-of-charter:** `OUT OF CHARTER (this arc): <item> — park for: <target arc/entity>`

Include `evidence_uris` pointing at the originating thread/turn when known. Set `derivation_type` appropriately (`commitment` for explicit deferrals, `inference` for agent-identified leftovers).

### 4. Association as later radar

Once parked, `activate` and graph traversal surface the residual when the entity is touched again — that is the memory loop. The imprint step is write-side; retrieval is read-side (boot salience is a separate deferred item).

## Anti-patterns

- Listing deferrals only in closeout prose or `/tmp` sidecars
- Opening the decision SOT to "remember" to park — the reflex should fire from the stub/skill, not SOT lookup
- Parking on a generic radar entity instead of the concerned matter
- Treating arc-close crosslinks as the only trigger — mid-session named residuals are the primary habit

## Related

- `decision:leftover-entity-association-parking` — posture SOT
- `session-close-audit` — pre-close mechanical check
- `checkpoint-discipline` — checkpoint-time residual sweep
