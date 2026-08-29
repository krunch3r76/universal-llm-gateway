---
name: continuity-thread-shaping
description: "On role:root birth, resume, catch-up, drop-on-liaison, hub wrap, or inbound LIAISON send — not work/worker/MONITOR: shape before details (thin card); walk one named hop with purpose; hub + ≥1 runbook; inbound acts by pointer."
skill_category: orchestration
trigger_short: "role:root house ∨ inbound LIAISON/NOTE on liaison-inbox"
trigger_match_terms:
  - continuity-thread-shaping
  - continuity card
  - thin card
  - thin card motif
  - house runbook
  - catch-up shape
  - continuity hub
  - drop this on the liaison
  - where do we leave this
  - standing root birth
  - resume preamble
  - sit-journal
  - liaison inbox
  - runbook-recall
  - LIAISON
  - operator-relayed
  - inbound LIAISON
  - TYPE: NOTE
  - fyi:
related_skills:
  - checkpoint-discipline
  - operator-posture
  - agent-bus-discipline
  - residual-imprint
  - ulg-for-llms
  - cortex-orientation
  - teach-once-routine-mint
  - mission-operator
---

# Continuity Thread Shaping

`spine=root ⇒ house(thin_card ∧ hub ∧ |runbook|≥1) ∧ depth=cortex/sidecars`
`liaison-inbox ∧ inbound(web-anthropic) ⇒ mark(act) ≺ send`
CHECKPOINT = reconstitution **index** (`checkpoint-discipline`). This skill owns the **thin-card motif** on `role:root` houses + hub wrap + runbook association. ¬ field IDs. ¬ a second profile. ¬ DIRECTIVE fields on this root. ¬ agent-bus code.

## Motif — thin card

**Shape before details. Walk to fill context — with purpose and intent.**

```
see(structure · shape · why)  ≺  walk(named next)
walk ⇒ purpose   # why this hop, what it is for
¬dump            # a linear file, a full entity, a recall spray
```

The agent first sees what *kind* of house this is (mission, runbooks, rules-as-ids, where the tape lives). Then it walks only the next named thing that serves the intent of *this* resume — reconstituting the sleeve, loading lane law, probing the book — not everything the graph can reach.

This is a **reading / cognition** motif, not an `entity_get(intent=card)` product. Cortex cards, short bus turns, and a house catch-up file are *instances*: they show structure first. Equating the motif with a Cortex card projection is a miss (operator 2026-08-28).

On this skill's surface the instance is the **house card**: headings that are the shape (Stance · Why · Objective · Runbooks · Rules-as-ids · Sidecars · House). Sit tape, Windows, numbers, essays stay off that shape so a later seat can walk them on purpose.

## When / Scope

`role:root` birth · resume of **that** root (`resume <n>` only when `n` is that root) · catch-up / card shape · "drop this on the liaison" · "where do we leave this" · hub wrap · inbound `LIAISON` / `NOTE` / `fyi:` on a liaison-inbox root.
¬ work-spine · ¬ conductor packet/worker · ¬ MONITOR / continuity-sibling · ¬ Child lane · ¬ dispatch thread.
Conductor *operates* the root (`conductor`); this skill *shapes the root's house*.

## Cortex-house order

```
see the house card              # shape: mission · runbooks · rules-as-ids · sidecar names
→ name the walk purpose         # e.g. reconstitute sleeve · load lane law · probe the book
→ walk only the next named hop  # runbook body · one sidecar · one probe — not the neighborhood
→ tip CHECKPOINT
```

`recall(op=continuity)` is **not** in this order (it dumps). Matter questions may still call it. A walk without a stated purpose is a dump with extra steps.

**Pointers, not status copies.** A house runbook or card **names** the matter `todo:` / `decision:` to walk — it does not copy `T14 open` / `unwired`. If that card is still `open`, **probe the locus** (path + content) before treating the work as undone. Open card + SHA in tree = stale card, not undone work. Stamp lives on the matter entity (`implement-todo` §5 · `cursor-sdk-instruction-standard` D3).

Life: shape first. Forbidden: reconstruct from chat · linear thread read · stuffing work on the open liaison · paste tool recipes or `## Windows` · treating a fat continuity-doc as the constitution.
Cite: `operator-posture` Rule 1.

## Card

Seven headings, one line each: Stance · Why · Objective · Runbooks · Rules · Sidecars · House. Essays and numbers leave the card. `## Rules` = id rows; missing heading = house gap; `_None yet._` legal. Shape detail → `card-schema.md`.

Reader-job body lives on a sidecar or the house runbook; the card points. Specimens 9582 · 9638 · 9732.

## Hub + runbook + edges

One `document:` per root: `{matter-slug}` (matter notebook) or `{N}-continuity` (house / code / conjurer / liaison). Never both. `source_uri` = card · `content_hash` = card sha · description = Objective + In/Out + `agent-bus:{N}` · `tag_assign continuity-root`. Birth: same write as charter surfaces. Re-root ⇒ `succeeds` + source_uri update. `tick_charter` roots get a hub; scoreboard = `evidence_uris`, ¬ a second entity. Hub mutates only on Objective rebind + checkpoint myelinate.

≥1 `runbook:*` edge per root or house_gap. House-specific (`runbook:{N}-house` / `runbook:{matter-slug}`) when the lane has law that fails the L2 test; shared counts but does not replace it. Per-matter clocks/prices/books live there. Mint body per `teach-once-routine-mint` § Author runbook body.

| Edge | Target |
|---|---|
| `references` | `runbook:*` (≥1) · `agent-bus:{N}` when minted |
| `related_to` | house entity · matter todos/decisions |
| `sibling_of` / `succeeds` | related / successor roots |

Residuals: `residual-imprint`. Hub ≠ parking lot.

## Triggers → acts

| Trigger | Act |
|---|---|
| Birth | seven headings + hub (`source_uri`=card) + ≥1 `runbook:*` · house-specific runbook same write if the lane has its own law → `card-schema.md` |
| Resume | Cortex-house order. Speaking it? Slot order = `operator-posture` Rule 3; fill sources → `resume-fill.md`. Slot 6 (imprint ids as memory) is the only slot this skill originates. IDE tab `{n} {slug}` |
| "where do we leave this" | matter node first → hub `related_to` if the house owns it → else new root |
| Inbound LIAISON / NOTE | per `liaison-inbound.md` · `send` ¬ `request` · `runbook:liaison-seat-on-a-lane` |

## L2 test

*Would this line still be true as a `runbook:` for one matter?* ⇒ it goes there, pointed from the card — ¬ this skill.

## Anti-patterns

| Bad | Good |
|---|---|
| Fat continuity-doc as constitution | Shape first (house card) · walk named hops with purpose |
| Equate the motif with `entity_get(intent=card)` | Cognition: structure ≺ details; Cortex card is one instance |
| Skill that describes cards but ships a file schema | Motif first; header is the house-card instance laid out |
| Walk the whole neighborhood "to be thorough" | Name the purpose; one hop |
| Copy `T14 open` / `unwired` onto the house runbook | Point at `todo:…`; walk + probe if the card is still open |
| Boot through `recall(op=continuity)` | Hub card → runbooks → sidecars |
| Missing `## Rules` / `## Runbooks` then follow a skill omit-path | House gap — mint before dispatch |
