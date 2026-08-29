---
name: continuity-thread-shaping
description: "On role:root birth, resume, catch-up, drop-on-liaison, hub wrap, or inbound LIAISON send — not work/worker/MONITOR: shape catch-up, mint hub, fill resume slots, mark inbound acts."
skill_category: orchestration
trigger_short: "role:root house ∨ inbound LIAISON/NOTE on liaison-inbox"
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

`spine=root ⇒ house(file ∧ hub) ∧ shape(body) ∧ fill(slots from durable surfaces)`
`liaison-inbox ∧ inbound(web-anthropic) ⇒ mark(act) ≺ send`
CHECKPOINT = reconstitution **index** (`checkpoint-discipline`). This skill owns catch-up **body** + hub wrap. ¬ field IDs. ¬ a second profile. ¬ DIRECTIVE fields on this root.

## When

Standing-root (`role:root`) birth · resume of **that** root (`resume <n>` only when `n` is `role:root`) · catch-up file shape · "drop this on the liaison" · "where do we leave this" · wrapping **that root** as a Cortex entity · inbound `LIAISON` / `NOTE` / `fyi:` send onto a liaison-inbox root.

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

## Header schema (catch-up file — ¬ CHECKPOINT field IDs)

This skill owns the **file** header. CHECKPOINT B1–B5 stay in `checkpoint-schema-profiles`. Birth mints the whole header in one write. Resume loads it before any `team_dispatch`.

| Heading | Required | Owns |
|---|---|---|
| `## Stance` | yes (`orchestrator_continuity`; `tick_charter` skips speech) | Use `ulg-for-llms` |
| `## Why this house` | yes | this-arc why |
| `## Objective` | yes | Mission + In / Out |
| `## Rules` | yes | lane law that **overrides** global skill omit-paths |

Newest-first journal prepends **below** this header — never above it. Body below may shape. One schema, one file per root.

**Bounded archive:** older `# Current` cuts fold into `## Archive` on the **same** file when the header + newest cut no longer fit a `fs read offset=0 limit≈120` budget. ¬ a second file (`{N}-window-ledger` / `{N}-recall.md`). Specimen drift: 9582 opens with `# Current`, header buried (~line 859) — reorder header-first on next touch; missing `## Rules` is a house gap (not legal), not this skill's mint on that root.

### `## Rules` (field)

```
## Rules
∀ continuity-doc:
  lane_law that a later seat must obey before team_dispatch
  ≺ global skill omit-path
absence ⇒ house_gap  # ¬ license to follow the card/skill default
```

| | |
|---|---|
| **Holds** | Dispatch knobs (e.g. T1 `fast=true`) · hop remainder (score → Conductor) · seat register (liaison ≠ conductor) |
| **Shape** | Table `Rule \| Bind`, or short MUST lines. No recipes. No leftover execute holds. |
| **Birth** | Same write as Stance / Why / Objective. Empty table + `_None yet._` is legal; **missing heading is not**. |
| **Resume** | Fill-map slot 5b. Read before fire. |
| **Tip** | CHECKPOINT indexes a pointer when a rule **changes**; speech stays on this file. |
| **Specimen** | 9638 |

Mission text SOT = file `## Objective`. Hub mirrors same write. Speech order: `operator-posture` Rule 3 · `decision:continuity-resume-mission-open`. Drift: hub `content_hash` ≠ file sha.

## Resume fill map

Slot **order** = `operator-posture` Rule 3. This skill owns **fill sources**.

| Slot | Filled from | ¬ |
|---|---|---|
| 1 Mission | file `## Objective` · hub description | slug · seeded todo |
| 2 In / Out | same block | widen silently |
| 3 been / are | tip `## State` + Handoff pointer | `## Windows` · linear thread |
| 4 `In one line:` | tip Handoff *State (1 line)* | omit the label |
| 5 Settled / Live / Next | newest `# Current` full cut · tip `## Next pickup` | overwrite snapshot tables · inventory dump · scoreboard paste |
| 5b Lane rules | file `## Rules` | omit and follow a global skill omit-path |
| 6 Tab + reconstitution | tip CHECKPOINT turn# · leftover imprint assertion/edge ids on hub or matter · resume CITE turn | chat as memory |
| 7 What I need from you | `OPERATOR_GATE` / `HOLD_MERGE` · hub deadlines | model-seat work · direction quiz |

Slot 6 is the only slot this skill originates — spoken form of "the imprint is already on the graph."

## Body shapes (specimen-gated)

| Reader job | Body | Default edges | Specimen |
|---|---|---|---|
| sit-journal | newest-first `# Current` below header | `related_to` worker todos | 9582 |
| liaison-inbox | **Recall** = newest-first `# Current` **full cut** (keys `score · stop · land · live · break · next`), written on `status` / `follow up` / every harvest, never edited. Inbox `owner=` table stays the **drop list**, not the recall surface. Inbound: table below. **Temporary** drop list, ¬ default foreign-work dump. | `references` decisions/frictions | 9638 |
| runbook-recall | runbook table + hazards | `references` `runbook:*` | 9732 |

A row exists only with ≥1 living specimen. `screenshot-spec` is a sidecar kind, ¬ a catch-up shape.

### Liaison-inbox inbound (BINDING)

`from=web-anthropic` on this root is overloaded. Marker + provenance, ¬ DIRECTIVE fields. Specimen: 9638#187.

| Act | Shape | Weight |
|---|---|---|
| Operator-relayed input to the liaison | subject `LIAISON —` · `send` ¬ `request` · first line `operator-relayed` (voice / chat) · `to=cursor` | Commission. ACK. ¬ `TYPE: DIRECTIVE` |
| Seat-own counsel to the lane agent | `TYPE: NOTE` or `fyi:` | Advisory. ¬ BIND |
| Unmarked web-anthropic prose | chatter / dialectic | Child work thread + one-line pointer on the root |
| Operator-proxy | `TYPE: DIRECTIVE` | Private request lane only. Never this root |

`unmatched ≠ chatter` on this root — authority also arrives as `BIND` · `CHECKPOINT` · `SCORE_RESURFACE`. Invert (typed parsers skip unmatched) is `mission-operator` law on the request lane, ¬ here.

## Triggers → acts

| Trigger | Act |
|---|---|
| Birth | header schema (Stance · Why · Objective · Rules) + hub + edges (same write as charter surfaces) |
| Resume | Cortex-house order + fill map. Only when the thread is `role:root`. IDE: tab `{n} {slug}` (`operator-posture`) |
| "where do we leave this" | matter node first → hub `related_to` if the house owns it → else new root |
| Inbound LIAISON / NOTE onto liaison-inbox root | Mark per inbound table; `send` ¬ `request` |

## Never in L2

Numbered execute paths · tool recipes · bus commands · per-matter clocks/prices/books · resume *prose* · tip-fetch mechanics · cortex op catalog (`cortex-orientation`) · residual claim shapes (`residual-imprint`) · CHECKPOINT field IDs.

Test: *would this line still be true as a `runbook:` for one matter?* ⇒ pointer row in the catch-up file, ¬ this skill.

## Anti-patterns

| Bad | Good |
|---|---|
| Stuff unrelated work on the open liaison | Matter node or new root |
| Reconstruct from chat | Hub → file → tip |
| Copy the full template when a `# Current` journal meets the reader | Header-first + sit-journal below |
| Overwrite a mutate-in-place scores / pickup snapshot | Prepend a `# Current` full cut; prior entry is the audit |
| Mint `{N}-window-ledger.md` / `{N}-recall.md` or a second CHECKPOINT profile | One schema, one file, body may shape; older cuts → same-file `## Archive` |
| Both `{matter-slug}` and `{N}-continuity` for one root | One hub |
| Mint `{N}-continuity` for a work / conductor-worker / MONITOR thread | House the root; pointer the worker |
| Hub as leftover parking lot | `residual-imprint` on the matter |
| Bury lane law under Living house / MAY notes | `## Rules` in the header schema |
| Missing `## Rules` then follow a skill omit-path | House gap — mint the heading before dispatch |
| `TYPE: DIRECTIVE` on a shared liaison root | Private request lane (`mission-operator`) |
| Field-schema a LIAISON like DIRECTIVE | Marker + provenance only |
| Treat NOTE / unmarked prose as operator BIND | Weight per inbound table |
| Treat non-DIRECTIVE on this root as chatter | LIAISON / BIND / CHECKPOINT still bind |
