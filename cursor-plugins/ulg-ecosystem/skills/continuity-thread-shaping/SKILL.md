---
name: continuity-thread-shaping
description: "On continuity-root (role:root) birth, resume, catch-up, drop-on-liaison, or hub wrap — not work/conductor-worker/MONITOR: shape catch-up body, mint/associate the document hub, fill resume slots."
skill_category: orchestration
trigger_match_terms:
  - continuity-thread-shaping
  - catch-up shape
  - continuity hub
  - drop this on the liaison
  - where do we leave this
  - standing root birth
  - resume preamble
  - sit-journal
  - liaison inbox
  - runbook-recall
related_skills:
  - checkpoint-discipline
  - operator-posture
  - agent-bus-discipline
  - residual-imprint
  - ulg-for-llms
  - cortex-orientation
  - teach-once-routine-mint
---

# Continuity Thread Shaping

`spine=root ⇒ house(file ∧ hub) ∧ shape(body) ∧ fill(slots from durable surfaces)`
CHECKPOINT = reconstitution **index** (`checkpoint-discipline`). This skill owns catch-up **body** + hub wrap. ¬ field IDs. ¬ a second profile.

## When

Standing-root (`role:root`) birth · resume of **that** root (`resume <n>` only when `n` is `role:root`) · catch-up file shape · "drop this on the liaison" · "where do we leave this" · wrapping **that root** as a Cortex entity.

## Scope

This skill = `role:root` house (file + `document:` hub + catch-up body).
¬ work-spine · ¬ conductor packet/worker · ¬ MONITOR / continuity-sibling · ¬ Child lane · ¬ dispatch thread.
Conductor *operates* the root (`conductor`); this skill *shapes the root's house*.
`resume <n>` only when `n` is that root.

## Cortex-house order

```
recall(op=continuity) ∨ entity_get(hub)
→ activate
→ fs(source_uri) header-first, then body
→ tip CHECKPOINT
```

Life: `recall` first. Forbidden: reconstruct from chat · linear thread read · stuffing work on the open liaison · paste tool recipes or `## Windows`.

## Hub — one per root, never both

| Root kind | `document:` id |
|---|---|
| Matter notebook that outlives a thread | `{matter-slug}` |
| House / code / conjurer / liaison | `{N}-continuity` |

`source_uri` = catch-up file · `content_hash` = file sha · description = Objective + In/Out + `agent-bus:{N}` · `tag_assign continuity-root`. Birth: same write as charter surfaces (extends `agent-bus-discipline` birth (1)). Re-root ⇒ `succeeds` + source_uri update. `tick_charter` roots get a hub; scoreboard = `evidence_uris`, ¬ a second entity. Hub mutates only on Objective rebind + checkpoint myelinate.

## Association

| Edge | Target |
|---|---|
| `references` | `runbook:*` · `agent-bus:{N}` when minted |
| `related_to` | matter todos/decisions the house drives |
| `sibling_of` / `succeeds` | related / successor roots. Same Objective ⇒ reuse; new Objective ⇒ new root + `succeeds` |

Residuals park on the **matter** node (`residual-imprint`). Hub ≠ parking lot.

## Header-first

`## Stance` → `## Why this house` → `## Objective` (+ In/Out) precede any body. Newest-first journal prepends **below** the header. One schema, one file per root; body below may shape.

Mission text SOT = file `## Objective`. Hub mirrors same write. Speech order: `operator-posture` Rule 3 · `decision:continuity-resume-mission-open`. Drift: hub `content_hash` ≠ file sha.

## Resume fill map

Slot **order** = `operator-posture` Rule 3. This skill owns **fill sources**.

| Slot | Filled from | ¬ |
|---|---|---|
| 1 Mission | file `## Objective` · hub description | slug · seeded todo |
| 2 In / Out | same block | widen silently |
| 3 been / are | tip `## State` + Handoff pointer | `## Windows` · linear thread |
| 4 `In one line:` | tip Handoff *State (1 line)* | omit the label |
| 5 Settled / Live / Next | file `## Settled` · tip `## WIP` · tip `## Next pickup` | inventory dump · scoreboard paste |
| 6 Tab + reconstitution | tip CHECKPOINT turn# · leftover imprint assertion/edge ids on hub or matter · resume CITE turn | chat as memory |
| 7 What I need from you | `OPERATOR_GATE` / `HOLD_MERGE` · hub deadlines | model-seat work · direction quiz |

Slot 6 is the only slot this skill originates — spoken form of "the imprint is already on the graph."

## Body shapes (specimen-gated)

| Reader job | Body | Default edges | Specimen |
|---|---|---|---|
| sit-journal | newest-first `# Current` below header | `related_to` worker todos | 9582 |
| liaison-inbox | `owner=` next-pickup; rows = pointers. **Temporary**, ¬ default foreign-work drop | `references` decisions/frictions | 9638 |
| runbook-recall | runbook table + hazards | `references` `runbook:*` | 9732 |

A row exists only with ≥1 living specimen. `screenshot-spec` is a sidecar kind, ¬ a catch-up shape.

## Triggers → acts

| Trigger | Act |
|---|---|
| Birth | header + hub + edges (same write as charter surfaces) |
| Resume | Cortex-house order + fill map. Only when the thread is `role:root` |
| "where do we leave this" | matter node first → hub `related_to` if the house owns it → else new root |

## Never in L2

Numbered execute paths · tool recipes · bus commands · per-matter clocks/prices/books · resume *prose* · tip-fetch mechanics · cortex op catalog (`cortex-orientation`) · residual claim shapes (`residual-imprint`) · CHECKPOINT field IDs.

Test: *would this line still be true as a `runbook:` for one matter?* ⇒ pointer row in the catch-up file, ¬ this skill.

## Anti-patterns

| Bad | Good |
|---|---|
| Stuff unrelated work on the open liaison | Matter node or new root |
| Reconstruct from chat | Hub → file → tip |
| Copy the full template when a `# Current` journal meets the reader | Header-first + sit-journal below |
| Mint `{N}-window-ledger.md` or a second CHECKPOINT profile | One schema, one file, body may shape |
| Both `{matter-slug}` and `{N}-continuity` for one root | One hub |
| Mint `{N}-continuity` for a work / conductor-worker / MONITOR thread | House the root; pointer the worker |
| Hub as leftover parking lot | `residual-imprint` on the matter |
