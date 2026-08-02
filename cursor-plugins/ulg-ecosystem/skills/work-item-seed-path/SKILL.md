---
name: work-item-seed-path
description: "Friction/idea → closable work item for /layer: recon?, architecture?, mint, graph attach. Use before /layer when no todo yet; feature-add or investigate+fix."
lifecycle: active
trigger_match_terms:
  - work-item-seed-path
  - work-item-seed
  - /work-item-seed
  - seed path
  - Fable-before-seed
  - rich-seed before layer
  - Stage 0 mint
  - seed work item
related_skills:
  - abstraction-layering
  - friction-review
  - cheap-recon-before-escalation
  - consult-routing
  - implement-todo
---

# Work-item seed path

`∀ codework lacking a closable todo: run this path ≺ /layer`. Feeds `/layer`; ¬ replace
`path-sim` or `/layer` G1–G6. Full stage rationale: `cortex://notes/system/specs/work-item-seed-path.md`.

## Invariant

```
friction|idea → S1…S6 → todo|{backlog|hold}
S3 mode B ⇒ attach(derived_from→consult_kind=architecture) ≺ claim G1 skip
S3 mode B claim ⇒ same_turn(admit(execution_id,poll_hint) ∨ honest_halt) — ¬ announce-only
¬ dispatch /layer before mint (unless subsume/divert exit ∧ ¬ Mode B)
rich_seed_field_lists ∈ /todo ∨ decision:todo-creation-rich-seed-contract — ¬ fork here
```

## When

| Condition | Route |
|---|---|
| Need new closable `todo:` (or backlog park) for feature/bug | This path |
| Architecture open; rich-seed would harden wrong shape | This path · prefer **S3 mode B** |
| Actionable friction; mint is next act | This path (cite `a:{id}`) |
| Todo already exists ∧ **no** Mode B / arch-consult ask | `/layer todo:{slug}` |
| Todo already exists ∧ Mode B mandatory | **This path** · Mode B on existing slug · ¬ remint · S5 → `/layer` G2 |
| Log-only gap, no change asked | `friction()` only · **exit** |
| Non-codework Q→A | `/path-sim` |
| Multi-phase plannable | `/plan-seed` |
| Settled ship-only | `/address` |

## Kinds (attribute ¬ rival paths)

| Kind | Intake | Default `density_triage` |
|---|---|---|
| **feature-add** | Idea / feature (usually ¬ friction) | `judgment_required` unless mechanical |
| **investigate+fix** | Defect / "fix this" (often friction+todo) | `recon_pending`∨`judgment_required`; `mechanical` iff locus+fix known |

Stamp kind on todo attrs/description until work_item registry lands.

## Stages

```
S1 Intake → S2 Recon? → S3 Architecture? → S4 Mint → S5 Attach → S6 /layer handoff
```

Skips are stage skips on **one** path.

### Stage disposition gate (BINDING — G2 falsifier mode-thrash)

**Before S4 mint:** publish a stage disposition table (S1–S6 rows, FIRE/SKIP + one-line why).
**Halt:** if S3 mode B is required (below) and no harvested consult URI exists → **¬ S4**;
fire `team_dispatch(model=cdp/fable|cdp/opus-5)` first, harvest, then resume.

**Mode B admit-proof (BINDING — friction announce-without-admit):** when Mode B is mandatory,
the same turn that claims Mode B disposition MUST end with either (a) `team_dispatch` admit
fields `execution_id` + `poll_hint` quoted from the tool response, or (b) an honest halt naming
the blocker. **Forbidden:** "staging then firing" / intent prose with no admit and no halt.
Poll/harvest may continue on later turns; **admit itself is same-turn.**

**S3 mode B mandatory when ANY** (even if `mode=` omitted):

| Trigger | Meaning |
|---|---|
| `mode=fable-before-seed` on entry | operator/command explicit |
| Operator names Fable / architectural consult / consult-before-seed / architecture-open / mode-B dogfood | explicit (incl. "include fable architectural consult") |
| S2 recon lists **≥2 open architecture forks** | co-primary shapes unranked |
| `feature-add` ∧ invariant-touching shape (e.g. inv 22 planes, pager/story/bus ownership) | premature rich-seed locks wrong plane |

**Mode A opt-in only:** `mode=seed-then-layer` on entry — otherwise do **not** default to A when
any row above matches. Omitting `mode=` is **not** license to skip Fable when S3 fires.

### S1 Intake

Classify kind · channel · subsumption.

| Check | Action |
|---|---|
| Log-only | `friction()` · exit |
| Fold under existing todo/task ∧ **¬** Mode B | Subsume · hand off `/layer` · exit |
| Fold under existing ∧ Mode B mandatory | Continue at S3 Mode B on **existing** slug · ¬ remint · after harvest S5 attach → S6 G2 |
| Plannable multi-phase | `/plan-seed` · exit |
| Else | Continue |

### S2 Recon? (optional)

**Fire:** locus unknown ∨ root cause open ∨ sparse idea/bug ∨ would be `recon_pending`.  
**Skip:** mechanical/trivial ∨ loci known ∨ `recon_waived`.  
**Do:** Use the `cheap-recon-before-escalation` skill. When S2 fires ∧ loci unknown / breadth open ⇒ **Explore subagent first** (`Task(subagent_type="explore")` per cheap-recon Tier-1) — ¬ Composer recon; ¬ in-seat Grep spray. Judgment residual after anchors → Grok / S3, not Explore.  
**Exit:** facts-only anchors + open forks. ¬ mint while ≥2 architecture rivals unranked.

### S3 Architecture? (optional)

**Fire:** rival shapes / detent≥wide ∨ invariant-touching ∨ consult-before-seed asked ∨ recon left ≥2 co-primaries.  
**Skip:** single obvious shape ∨ mechanical.  
**Do:** CDP architecture consult (`team_dispatch(model=cdp/fable|cdp/opus-5)` per consult-routing). Harvest to cortex.

| Mode | Sequence | Choose when |
|---|---|---|
| **A** seed→layer-G1 | S4 rich-seed → `/layer` G1 → harvest → S5 → G2 | Problem clear; stable slug; arch is layer-G1 concern |
| **B** Fable-before-seed | Consult+harvest → S4 informed mint → S5 → `/layer` **G2** | Premature rich-seed locks wrong shape; operator asked consult-first; nebulous strategy fork |

### S4 Mint (exactly one)

| Mode | When | What |
|---|---|---|
| **Rich-seed** | Actionable; slug+Problem/Scope/Acceptance nameable | Compose `/todo` rich-seed contract. Set `arc_lane=layer`, `density_triage`, `source_uri`, kind |
| **Backlog-park** | Trackable ¬ ready | `backlog=true` (or deferred) · ¬ `/layer` |
| **Hold** | ¬ backlog-worthy | Ack · optional friction · exit |

`¬` rich-seed pretending arch settled when mode B required.

### S5 Graph attach

**When:** consult answer exists (mode B, or mode A after layer-G1 harvest).  
**Do:** `document:{slug}-architecture-consult` (`consult_kind=architecture`, `source_uri`) + `todo --derived_from--> document` (structural).  
**Skip:** no consult.

### S6 `/layer` handoff

| Condition | Enter |
|---|---|
| `derived_from` → architecture doc resolves | `/layer` at **G2** (G1 skip + URI) |
| Rich-seeded; arch still open (A) | `/layer` at **G1** |
| `density_triage=mechanical` ∧ no higher gate | `/layer` **G5** |
| Backlog ∨ hold | **¬** `/layer` |

Name: `todo:{slug}` · entry gate · consult URI · recon URI.

## Kind deltas

| Stage | feature-add | investigate+fix |
|---|---|---|
| S1 | Usually ¬ friction | Prefer friction if tool/protocol gap |
| S2 | On sparse/unknown locus | **Default on** unless mechanical |
| S3 | Nebulous product/shape forks | Rival fix designs; skip if locus+fix known |
| S4–S6 | Same modes/handoff | Fix-cycle todo; spine after mint per friction-review |

## Anti-patterns

| Bad | Good |
|---|---|
| Fable answer on disk, no `derived_from` | S5 attach ≺ G1 skip claim |
| `/layer` before mint | S4 first (or B: consult→mint) |
| Feature vs bug as two workflows | One path; kind attribute |
| Copy rich-seed field lists into this skill | Point `/todo` + rich-seed decision |
| `/layer` on backlog | Park until promote + re-enter |
| Non-code Q on this path | Divert `/path-sim` at S1 |
| Bare `/work-item-seed` with no idea, or mint before stage table | Halt — need idea text; publish disposition; run S3 when mandatory |
| S3 fired ∧ no Fable harvest | Minting or `/layer` — harvest consult first |
| Announce Mode B / "staging Fable" then end turn with no admit | Same-turn admit (`execution_id`+`poll_hint`) or named halt |
| Existing todo + Mode B ask → divert to bare `/layer` | Mode B on existing slug · attach · `/layer` G2 |
| S2 breadth via Composer or in-seat Grep spray | Explore subagent first (cheap-recon Tier-1) |

## Commissioning register (operator bind 2026-08-02)

This path is the **default shape for handing an idea to `cursor/grok-4.5` as sub-PM** —
grok receives the idea in the register a lead receives it and drives S1–S6 itself,
fanning out to Explore (S2 breadth), Composer (mechanical leg), Opus/Fable (S3 fork it
cannot rank), and grok again (parallel seeds). `¬` a hard rule — an emergent shape; the
commissioner keeps judgment on when to bind directly.

`commission(idea) ≻ commission(decomposition)` — the commissioner's job is **enablement**:

| Field on the commission | Why |
|---|---|
| `Use the work-item-seed-path skill` | headless entry surface — ¬ the IDE command |
| kind: `feature-add` \| `investigate+fix` | sets the S2 default (investigate+fix is recon-on) |
| known anchors / loci | lets S2 be legitimately **skipped** ¬ re-derived |
| S3 Mode B mandatory? | if yes, the admit-proof rule binds (same-turn admit or halt) |
| expected S6 entry gate + harvest shape | makes the closeout adjudicable |

Cadence: fewer, fatter commissions amortize round-trip latency vs. paying it per
micro-step. Dispatch shape: `seat=cursor-sdk` · `model=cursor/grok-4.5` ·
`model_knobs={"effort":"high","fast":"false"}` (catalog default is **`fast=true`**;
`reasoning_effort` is rejected 422 on `seat=cursor-sdk`). Life seats cannot call
`team_dispatch` — commission via `agent_bus.request` and name the knobs in the body.
Operator-proxy SOT: `libs/claude_bundles/operator_proxy_mission.py` § Idea commissioning.

## Entry surfaces

| Surface | Form |
|---|---|
| Cursor IDE | `/work-item-seed …` (thin command) |
| Headless / Auto / CDP | Use the `work-item-seed-path` skill |
| Commissioned to grok sub-PM | `agent_bus.request` (life) or `team_dispatch` (code) — see § Commissioning register |

```
/work-item-seed {idea}
/work-item-seed friction a:{id}
/work-item-seed mode=fable-before-seed {idea}
```
