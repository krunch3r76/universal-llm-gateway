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
  - attended charter birth
  - standing coding mission
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

## Operator surface

Slash commands (`/work-item-seed`, `/layer`) are **wrappers**. Natural language
fires the same path — “loop Fable in”, “architectural guidance”, “feature
addition”. Do not wait for a slash.

## When Fable defaults (CDP — not a Cursor pool)

Fable is `cdp/fable` (web product). It is **not** in Cursor Models. `cursor/claude-fable-5`
is a carded Other Models option; we **block** it for cost — do not pin it. Use `cdp/fable`.

| Default Fable | When |
|---|---|
| **This path Mode B** + `/layer` G1 | Architecture open: named Fable / architectural consult / ≥2 unranked forks / feature-add ∧ invariant-touching / detent≥wide. Recipe: § S3 |
| **Path-sim Q** | Non-codework bundled Q — not this path |
| **Ladder 2b** | Independent binder when the producer is Opus |

**Skip Fable:** G1 skip edge resolves · S3 skip (single obvious / mechanical) · Mode A opt-in (`mode=seed-then-layer`; G1 still owed later) · feature not commissioned.

### Cursor pools — do not substitute for Fable G1

| Pool | Models | Job on a codework arc |
|---|---|---|
| **Cursor Models** | Grok-4.6, Composer 2.5 | G3 densify / G5 implement / T1 orchestrate / CDP-stuck 2b default — **after** Fable harvest |
| **Other Models (secondary)** | Sonnet 5, Opus-in-cursor, Terra, Sol, Luna, `cursor/claude-fable-5` | **Explicit pin only** (cost). Includes Terra. `cursor/claude-fable-5` **blocked** (cost) → `cdp/fable`. ¬ silent G4 / ladder 2c / reviewer / hop-5 default. |

G2 frame is **CDP Opus**, not the Other Models pool. Other Models quota is not a reason to skip CDP Fable G1 or to spend T3 Opus as a Fable substitute. T2/T3 and hop-4 live-checkout Opus still need their **named** trigger — they are not silent defaults.

## When

| Condition | Route |
|---|---|
| Need new closable `todo:` (or backlog park) for feature/bug | This path |
| Architecture open; rich-seed would harden wrong shape | This path · prefer **S3 mode B** |
| Operator “loop Fable” / architectural guidance / named Fable (even if they also said `/layer`) | This path · **Mode B** · copy § S3 Fable generate recipe |
| Actionable friction; mint is next act | This path (cite `a:{id}`) |
| Todo already exists ∧ **no** Mode B / arch-consult ask | `/layer todo:{slug}` |
| Todo already exists ∧ Mode B mandatory | **This path** · Mode B on existing slug · ¬ remint · S5 → `/layer` G2 |
| Log-only gap, no change asked | `friction()` only · **exit** |
| Feature ask, design open, work **not** commissioned | `friction(category=feature)` · **exit** — ¬ S4 |
| Non-codework Q→A | `/path-sim` |
| Multi-phase plannable | `/plan-seed` |
| Settled ship-only | `/address` |
| Standing coding mission (charter / scoreboard / vision-align / new root) | `cortex://notes/system/playbooks/attended-charter-birth-with-cursor.md` — propose → vision-align → operator ratify → scoreboard → root. Worked example: agent-bus:7281. ¬ this seed path; ¬ implement before ratify |

## Kinds (attribute ¬ rival paths)

| Kind | Intake | Default `density_triage` |
|---|---|---|
| **feature-add** | Idea / feature (often `friction(feature)` first; mint only when commissioned) | `judgment_required` unless mechanical |
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
fire Mode B transport (below) first, harvest, then resume.

**Mode B transport (BINDING — live CSE ≻ fresh generate; friction a:27616 / bus 6737):**
when an **attached live operator-proxy CSE** exists (identity ladder
`chat_url ≻ registration_id ≻ execution_id`), Mode B / CDP architecture consult MUST use
`cse_session(op=followup, purpose=operator-proxy, …)` into that CSE —
**¬** fresh `team_dispatch(model=cdp/fable|cdp/opus-5)`. Fresh `team_dispatch(model=cdp/…)`
only when no live attached CSE (continuity hop / new window / Customize refresh).
Compose `cdp-operator-proxy` inv 23.

**Mode B admit-proof (BINDING — friction announce-without-admit):** when Mode B is mandatory,
the same turn that claims Mode B disposition MUST end with either (a) transport admit —
`team_dispatch` `execution_id`+`poll_hint` **or** `cse_session(followup)` admit fields
quoted from the tool response — or (b) an honest halt naming the blocker.
**Forbidden:** "staging then firing" / intent prose with no admit and no halt.
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
**Do:** CDP architecture consult per Mode B transport above. Copy **§ S3 Fable
generate recipe** (followup vs fresh generate). **Before fire:** Fable/Opus G1 skill
floor — `architecture-invariants` ∧ `ulg-architecture` sealed (abstraction-layering
§ Fable / CDP G1 lead preflight); judgment chips alone ≠ floor. Harvest to cortex.

**Premium rung:** fork needs live-checkout verification at file:line depth a CDP seat
structurally cannot perform ⇒ escalate to `cursor/claude-opus-5` (hop 4) when the
four-condition trigger in `decision:architecture-bind-escalation-chain` holds
(`cdp-operator-proxy` § Architecture-bind chain) — **pre-authorized, ¬ operator ping**.
That trigger picks the **seat**, not a second effort gate; once picked, knobs follow
the model card. Hop 4 may recommend `{xhigh|max}` for that hop's duty. Announce
model + effort + why. That SOT also binds the **mandatory** independent
check (`cdp/fable` default; `cursor/gpt-5.6-terra` only if operator/packet names
Other Models — an Opus-authored architecture is not self-ratifiable) and verbatim
densify. ¬ fork those rules here.

| Mode | Sequence | Choose when |
|---|---|---|
| **A** seed→layer-G1 | S4 rich-seed → `/layer` G1 → harvest → S5 → G2 | Problem clear; stable slug; arch is layer-G1 concern |
| **B** Fable-before-seed | Consult+harvest → S4 informed mint → S5 → `/layer` **G2** | Premature rich-seed locks wrong shape; operator asked consult-first; nebulous strategy fork |

#### S3 Fable generate recipe (BINDING — sole copy-paste)

Canonical admit for Mode B **and** `/layer` G1. `/layer` cites this section — ¬ fork a
second block. Live CSE ≻ fresh generate (transport rule above).

**Followup** (attached operator-proxy CSE — `chat_url ≻ registration_id ≻ execution_id`):

```
cse_session(
    op="followup",
    purpose="operator-proxy",
    chat_url="<attached>",  # or registration_id / execution_id
    prompt_text=…,          # or prompt_uri=cortex://…
)
# same-turn: quote followup admit fields, or honest halt
```

**Fresh generate** (no live attached CSE):

```
manage(action="busy_status")  # serialize a second purpose=ask
team_dispatch(
    op="generate",
    model="cdp/fable",
    contract="light-bounded",
    purpose="ask",
    packet_path="tmp/reviews/{slug}-fable-g1.md",  # or sidecar_ref=cortex://…
    dispatch_thread_id="<work thread, not an unrelated charter root>",
    skills=["architecture-invariants", "ulg-architecture", "reasoning-posture"],
)
# same-turn: quote execution_id + poll_hint, or honest halt
```

`skills=` must include the arch pair; judgment chips alone ≠ floor. `purpose=ask` is
the architecture-consult tag — ¬ `operator-proxy` unless this *is* a mission followup.

### S4 Mint (exactly one)

**S4a — identity mint (IDE, before spawn):** mint closable `todo:` slug + kind +
`density_triage` only. Problem/Scope/Acceptance may be sparse. **Do not** set
`implement_ready`. **Do not** hand to `/layer` yet.

**S4b — rich-seed after G1 harvest (conductor):** after architecture consult
harvest, densify Problem/Scope/Acceptance on the **same slug**, hang
`derived_from`, update `density_triage` (still ≠ `implement_ready`), then
`ROW_PINNED` when `stop_after: G1` binds. Mode B admit-proof lives on conductor
CHECKPOINT (`execution_id`+`poll_hint` or honest halt) — not the IDE turn.

| Mode | When | What |
|---|---|---|
| **S4a identity** | First utterance / pre-spawn | `/todo` identity mint only |
| **S4b rich-seed** | G1-pin / post-harvest | Conductor densifies same slug |
| **Backlog-park** | Trackable ¬ ready | `backlog=true` (or deferred) · ¬ spawn |
| **Hold** | ¬ backlog-worthy | Ack · optional friction · exit |

`¬` rich-seed pretending arch settled when mode B required.

### S5 Graph attach

**When:** consult answer exists (mode B, or mode A after layer-G1 harvest).  
**Do:** `document:{slug}-architecture-consult` (`consult_kind=architecture`, `source_uri`) + `todo --derived_from--> document` (structural).  
**Skip:** no consult.

### S6 Spawn conductor (¬ `/layer` handoff)

First codework utterance after S4a mint:

```text
team_dispatch(
  seat="cursor-sdk",
  contract="light-bounded",
  lane="B",
  source_ref="todo:{slug}",
  packet_kind="conductor",
)
```

Receipt must quote `dispatch_id` + scoreboard URI + Lane B. Conductor drives
G-ladder; IDE does not fire `/layer` or Mode B Fable on the seed path.

| Condition | Enter |
|---|---|
| S4a identity mint complete | **Spawn** conductor (above) |
| Resume after terminal | Re-admit conductor at persisted G-row |
| Backlog ∨ hold | **¬** spawn |

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
| Announce Mode B / "staging Fable" then end turn with no admit | Same-turn admit (`execution_id`+`poll_hint` or followup admit) or named halt |
| Fable/Opus Mode B with only judgment skills (no `ulg-architecture`) | Seal arch pair per abstraction-layering G1 preflight before admit |
| Reconstruct the Fable generate from memory / path-sim Q | Copy § S3 Fable generate recipe (arch-pair `skills=` + `purpose=ask`) |
| Mode B under live operator-proxy CSE via fresh `team_dispatch(cdp/…)` | `cse_session(op=followup)` into attached CSE (inv 23); fresh CDP only if no live CSE |
| Existing todo + Mode B ask → divert to bare `/layer` | Mode B on existing slug · attach · `/layer` G2 |
| S2 breadth via Composer or in-seat Grep spray | Explore subagent first (cheap-recon Tier-1) |
| S3 fork needs checkout depth → park on the operator for premium approval | Four-condition trigger holds ⇒ fire hop 4 and announce; effort is the card |
| Premium S3 bind ratified by the seat that commissioned it | Independent check is mandatory (terra/Fable) — see the chain SOT |

## Commissioning register (operator bind 2026-08-02)

This path is the **default shape for handing an idea to `cursor/grok-4.6` as sub-PM** —
grok receives the idea in the register a lead receives it and drives S1–S6 itself,
fanning out to Explore (S2 breadth), Composer (mechanical leg), Opus/Fable (S3 fork it
cannot rank), and grok again (parallel seeds). `¬` a hard rule — an emergent shape; the
commissioner keeps judgment on when to bind directly.

`commission(idea) ≻ commission(decomposition)` — the commissioner's job is **enablement**:

| Field on the commission | Why |
|---|---|
| `Use the work-item-seed-path skill` | headless entry surface — ¬ the IDE command |
| kind: `feature-add` \| `investigate+fix` | sets the S2 default (investigate+fix is recon-on). Commission prose — AutoJob admit **defers** `kind:`, it does not bind it. |
| known anchors / loci | lets S2 be legitimately **skipped** ¬ re-derived |
| S3 Mode B mandatory? | if yes, the admit-proof rule binds (same-turn admit or halt) |
| expected S6 entry gate + harvest shape | makes the closeout adjudicable |

Cadence: fewer, fatter commissions amortize round-trip latency vs. paying it per
micro-step. **Life seats** commission via `agent_bus.request` with `desired_model` and
`desired_effort` on the **wire** — do not pin effort in the DIRECTIVE body
(`effort_pin_refused`). **Code-side** `team_dispatch` shape: `seat=cursor-sdk` ·
`model=cursor/grok-4.6` · `model_knobs={"effort":"high","fast":"false"}` on the dispatch
wire (catalog default is **`fast=true`**; `fast` has no wire param on `agent_bus.request`;
`reasoning_effort` is rejected 422 on `seat=cursor-sdk`). Operator-proxy SOT:
`libs/claude_bundles/operator_proxy_mission.py` § Knob relay.

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
