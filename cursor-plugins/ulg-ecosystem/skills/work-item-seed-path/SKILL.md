---
name: work-item-seed-path
description: "Commissioned codework → lookup existing todo, then S4a identity mint + spawn conductor only when none. Mode B is Fable-before-S4b, not Fable-before-identity. Slash on an already-punched idea is re-admit, not a second mint."
lifecycle: active
trigger_match_terms:
  - work-item-seed-path
  - work-item-seed
  - /work-item-seed
  - seed path
  - Fable-before-seed
  - Fable-before-S4b
  - spawn conductor
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

`∀ commissioned codework lacking a closable todo: lookup ≺ S4a identity mint ≺ spawn conductor`.
Hit on an open todo (slug / name / same-idea stem, including a same-session NL punch) ⇒ re-admit or halt-with-pointer · ¬ remint · ¬ second spawn.
S4b + G1–G6 live on the conductor. ¬ replace `path-sim` (non-codework). ¬ conductor
chooser for `/path-sim` vs `/layer`. Bind: `layer-conductor-unify` §3.1.
Rationale: `cortex://notes/system/specs/work-item-seed-path.md`.

**Keep identity separate?** **Yes for S4a** (punch — slug, kind, sparse one-liner,
`density_triage`). **No for S4b / G-ladder** (already inside the conductor). Halt-S4a
until Fable harvest is **stale**. `seed` in the slash name is the punch, ¬ Architect.

## Invariant

```
friction|idea → S1…S6 → todo|{backlog|hold}
S4a identity ≺ spawn conductor  # always, commissioned codework
S3 mode B = Fable-before-S4b    # ¬ Fable-before-identity
S3 mode B ⇒ attach(derived_from→consult_kind=architecture) ≺ claim G1 skip
S3 mode B admit-proof binds the conductor CHECKPOINT — ¬ the IDE seed turn
¬ spawn conductor before S4a (unless subsume/divert)
rich_seed_field_lists ∈ /todo ∨ decision:todo-creation-rich-seed-contract — ¬ fork here
```

## Operator surface

`/work-item-seed` is the **identity punch** (wrapper). `/layer` is gate-shape,
¬ a second admit. Natural language fires the same punch — “commission this”,
“loop Fable in”, “architectural guidance”. Do not wait for a slash.

## When Fable defaults (CDP — not a Cursor pool)

Fable is `cdp/fable` (web product). It is **not** in Cursor Models. `cursor/claude-fable-5`
and `cursor/claude-fable-5-1` (launched 2026-09-01, same headline $/M) are carded Other
Models options; we **block** both for cost — do not pin either. Use `cdp/fable`.

| Default Fable | When |
|---|---|
| **Conductor G1 / Mode B** | Architecture open: named Fable / architectural consult / ≥2 unranked forks / feature-add ∧ invariant-touching / detent≥wide. Recipe: § S3 — **conductor copies**; IDE seed path does not fire it |
| **Path-sim Q** | Non-codework bundled Q — not this path |
| **Ladder 2b** | Independent binder when the producer is Opus |

**Skip Fable:** G1 skip edge resolves · S3 skip (single obvious / mechanical) · Mode A opt-in (`mode=A` / alias `seed-then-layer`; G1 still owed later) · feature not commissioned.

### Cursor pools — do not substitute for Fable G1

| Pool | Models | Job on a codework arc |
|---|---|---|
| **Cursor Models** | Composer 2.5 | G3 densify / G5 implement / conductor orchestrate / CDP-stuck 2b default — **after** Fable harvest |
| **Other Models (secondary)** | Sonnet 5, Opus-in-cursor, Terra, Sol, Luna, `cursor/claude-fable-5{,-1}` | **Explicit pin only** (cost). Includes Terra. `cursor/claude-fable-5{,-1}` **blocked** (cost) → `cdp/fable`. ¬ silent G4 / ladder 2c / reviewer / hop-5 default. |

G2 frame is **Fable followup** in the G1 CSE (else `cdp/opus-5` fresh), not the Other Models pool. Other Models quota is not a reason to skip CDP Fable G1 or to spend T3 Opus as a Fable substitute. T2/T3 and hop-4 live-checkout Opus still need their **named** trigger — they are not silent defaults.

## When

| Condition | Route |
|---|---|
| Need new closable `todo:` (or backlog park) for feature/bug | This path · S4a then S6 spawn |
| Architecture open; rich-seed would harden wrong shape | This path · **S4a still fires** · stamp Mode B for conductor S4b |
| Operator “loop Fable” / architectural guidance / named Fable | This path · S4a → spawn · conductor owns Mode B / § S3 recipe |
| Actionable friction; mint is next act | This path (cite `a:{id}`) |
| Todo already exists ∧ **no** Mode B / arch-consult ask | Re-admit conductor at persisted G-row · ¬ remint · ¬ `/layer` |
| Todo already exists ∧ Mode B mandatory | Re-admit conductor on **existing** slug · ¬ remint · conductor G1 → S5 → G2 |
| `/work-item-seed {idea}` matches an open todo (exact slug, `todo:conductor-{idea}`, or name/stem) | Same as the two rows above — **S0 lookup** · ¬ punch a sibling |
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
| **investigate+fix** | Defect / "fix this" (often friction+todo) | `recon_pending`∨`judgment_required`; friction-sourced non-feature ⇒ never auto-`mechanical` (§ Friction-sourced override); non-friction defect: `mechanical` iff locus+fix known |

Stamp kind on todo attrs/description until work_item registry lands.

## Stages

```
S0 Lookup → S1 Intake → S2 Recon? → S3 Architecture? → S4a identity → S6 spawn conductor
         ↳ hit existing todo → re-admit / halt · ¬ S4a · ¬ S6
         ↳ (conductor) S3 Mode B / G1 → S4b rich-seed → S5 attach → G2…
```

Skips are stage skips on **one** path. S3 consult + S4b + S5 are **conductor-owned**.

### Stage disposition gate (BINDING — G2 falsifier mode-thrash)

**Before S4a:** run **S0 lookup**, then publish a stage disposition table (S0–S6 rows, FIRE/SKIP + one-line why). S0 hit ⇒ SKIP S4a and S6 in that table.
**Halt S4b / rich-seed** if S3 mode B is required and no harvested consult URI exists.
**¬ halt S4a** for Mode B — identity settles nothing. IDE on this path does **not**
fire Mode B Fable; spawn after S4a and let the conductor own admit-proof.

**Mode B transport (BINDING — conductor fires; live CSE ≻ fresh generate; friction a:27616 / bus 6737):**
when an **attached live operator-proxy CSE** exists (identity ladder
`chat_url ≻ registration_id ≻ execution_id`), Mode B / CDP architecture consult MUST use
`cse_session(op=followup, purpose=operator-proxy, …)` into that CSE —
**¬** fresh `team_dispatch(model=cdp/fable|cdp/opus-5)`. Fresh `team_dispatch(model=cdp/…)`
only when no live attached CSE (continuity hop / new window / Customize refresh).
Compose `cdp-operator-proxy` inv 23.

**Mode B admit-proof (BINDING — friction announce-without-admit):** when Mode B is mandatory,
the same **conductor CHECKPOINT** that claims Mode B disposition MUST end with either
(a) transport admit — `team_dispatch` `execution_id`+`poll_hint` **or**
`cse_session(followup)` admit fields quoted from the tool response — or (b) an honest
halt naming the blocker. **Forbidden:** "staging then firing" / intent prose with no
admit and no halt. Poll/harvest may continue on later turns; **admit itself is same-turn.**

**S3 mode B mandatory when ANY** (even if `mode=` omitted):

| Trigger | Meaning |
|---|---|
| `mode=B` / `fable-before-S4b` / `fable-before-seed` on entry | operator/command explicit |
| Operator names Fable / architectural consult / consult-before-seed / architecture-open / mode-B dogfood | explicit (incl. "include fable architectural consult") |
| S2 recon lists **≥2 open architecture forks** | co-primary shapes unranked |
| `feature-add` ∧ invariant-touching shape (e.g. inv 22 planes, pager/story/bus ownership) | premature rich-seed locks wrong plane |

**Mode A opt-in only:** `mode=A` (alias `seed-then-layer`) on entry — otherwise do **not** default to A when
any row above matches. Omitting `mode=` is **not** license to skip Fable when S3 fires.

### S0 Identity lookup (BINDING — before S4a)

`∀ /work-item-seed {idea}` and the same NL punch: **search before mint**.

1. Try `entity_get` on `todo:{idea}`, `todo:conductor-{idea}`, and any slug the idea already names.
2. `entities(type=todo, query=<idea tokens>)` for **open** rows whose slug or name shares the stem.
3. Same-session NL commission of that idea (friction + S4a already fired) counts as a hit even if the slash tokens are shorter than the slug.

| Hit | Do |
|---|---|
| Open todo, no live conductor | Re-admit on that slug (`reuse_thread` if a terminal worker is reusable) · ¬ remint · ¬ sibling spawn |
| Open todo, conductor in flight | Halt · name the worker · ¬ remint · ¬ second spawn |
| Open todo, terminal + leftover G-row (ROW_PINNED / PARKED / consult) | Re-admit `reuse_thread` when GIW can take it · ¬ remint |
| No hit | Continue S1 |

Specimen 2026-08-31: NL “keep track of usage by movement” punched `todo:conductor-usage-by-movement` + worker 9831; later `/work-item-seed usage-by-movement` must S0-hit, not punch a sibling.

### S1 Intake

Classify kind · channel · subsumption. S0 already ran.

| Check | Action |
|---|---|
| Log-only | `friction()` · exit |
| Fold under existing todo/task ∧ **¬** Mode B | Subsume · re-admit conductor · exit |
| Fold under existing ∧ Mode B mandatory | Re-admit conductor on **existing** slug · ¬ remint · conductor G1 → S5 → G2 |
| Plannable multi-phase | `/plan-seed` · exit |
| Else | Continue |

### S2 Recon? (optional)

**Fire:** locus unknown ∨ root cause open ∨ sparse idea/bug ∨ would be `recon_pending`.  
**Skip:** mechanical/trivial ∨ loci known ∨ `recon_waived`.  
**Do:** Use the `cheap-recon-before-escalation` skill. When S2 fires ∧ loci unknown / breadth open ⇒ **Explore subagent first** (`Task(subagent_type="explore")` per cheap-recon Tier-1) — ¬ Composer recon; ¬ in-seat Grep spray. Judgment residual after anchors → CDP / S3, not Explore.  
**Exit:** facts-only anchors + open forks. ¬ S4b / ¬ rich-seed while ≥2 architecture rivals unranked. S4a identity is allowed.

#### Friction-sourced override (BINDING — operator bind 2026-08-30)

`∀ /work-item-seed friction a:{id}`: S4a **must** stamp integer
`spawned_by_friction=<id>` (not `derived_from_friction`, not `"a:{id}"`).
Todo-done auto-`friction_close`s the parent only when that key parses as an
int (`_friction_followon_close`). Charter conveyor mint already writes it;
IDE seed must match.

`∀ /work-item-seed friction a:{id}` where the friction's `category != feature`:
stamp `bug_class_sweep_required=true` at S4a. This **removes** `mechanical` ∧
`loci known` as S2-skip justifications for that item — a known-locus/known-fix bug
can still sit inside a broader nest the exact locus doesn't reveal (same refactor,
same module); a bug-class grep alone finds textual duplicates, not that nest.
**Skip only** via explicit `recon_waived="<reason>"`. Recon scope may stay narrow
(module/file-level Explore pass, not full-service breadth) — narrow ≠ skip.
`bug_class_sweep_required` also gates conductor G6 (`abstraction-layering` § Gates)
— closeout must carry a labeled `## Secondary findings` block (contract:
`friction-review` § Pass zoom-out duty). `category == feature` frictions are
unaffected — S2 keeps its normal optional gate.

### S3 Architecture? (optional)

**Fire:** rival shapes / detent≥wide ∨ invariant-touching ∨ consult-before-seed asked ∨ recon left ≥2 co-primaries.  
**Skip:** single obvious shape ∨ mechanical.  
**Do (conductor, after spawn):** CDP architecture consult per Mode B transport above.
Copy **§ S3 Fable generate recipe** (followup vs fresh generate). Staging
`purpose=ask` seals the arch pair — do not reconstruct `skills=`. Harvest to cortex.
IDE seed path stamps Mode B on the disposition; it does not fire the consult.

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
| **A** seed→conductor-G1 | S4a → spawn → conductor G1 → harvest → S4b/S5 → G2 | Problem clear; stable slug; arch is G1 concern |
| **B** Fable-before-S4b | S4a → spawn → consult+harvest → S4b informed densify → S5 → **G2** | Premature rich-seed locks wrong shape; operator asked consult-first; nebulous strategy fork |

#### S3 Fable generate recipe (BINDING — sole copy-paste)

Canonical admit for **conductor** G1 / Mode B. Gate-shape reference (`abstraction-layering`)
cites this section — ¬ fork a second block. IDE seed path does **not** fire this.
Live CSE ≻ fresh generate (transport rule above).

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
)
# same-turn: quote execution_id + poll_hint, or honest halt
```

`purpose=ask` is the architecture-consult tag — staging merges the arch pair +
`reasoning-posture`. Caller `skills=` is additive only. ¬ `operator-proxy` unless
this *is* a mission followup. Stock skills are a prompt verb, never `skills=`.

### S4 Mint (exactly one)

**S4a — identity mint (IDE, before spawn):** mint closable `todo:` slug + kind +
`density_triage` only. Friction-sourced: also `spawned_by_friction=<int id>`.
Problem/Scope/Acceptance may be sparse. **Do not** set
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
  model_knobs={"fast":"true"},
  dispatch_thread_id="{root}",   # continuity root with turns — or pending-empty child of root
)
```

`dispatch_thread_id ∈ {continuity root with turns, pending-empty child of root}`.
Forbid lifecycle-null pre-create (422 `conductor_coord_split_refused`). Resume
after terminal: `reuse_thread=<work thread>` — do not re-pass the work thread as
`dispatch_thread_id` without `reuse_thread=`.

Receipt identity **is** the admitted thread with
`branch_current=cursor-sdk/lane-{that id}` — quote `dispatch_id` + scoreboard URI
+ that Lane B. Spawn is **Composer** (`{fast:true}`); judgment nests CDP.
Conductor
drives G-ladder; IDE does not fire `/layer` or Mode B Fable on the seed path.

| Condition | Enter |
|---|---|
| S4a identity mint complete | **Spawn** conductor (above) |
| Resume after terminal | Re-admit with `reuse_thread=<work thread>` at persisted G-row |
| Backlog ∨ hold | **¬** spawn |

Name: `todo:{slug}` · entry gate · consult URI · recon URI.

Conductor fault after spawn: operator names the worker/lane to the sitting
liaison (specimen 9638). That house debugs. ¬ fold seed into the liaison
drop list — the command stays punch-then-spawn.

## Kind deltas

| Stage | feature-add | investigate+fix |
|---|---|---|
| S1 | Usually ¬ friction | Prefer friction if tool/protocol gap |
| S2 | On sparse/unknown locus | **Default on** unless mechanical |
| S3 | Nebulous product/shape forks | Rival fix designs; skip if locus+fix known |
| S4–S6 | S4a then spawn | Same; spine after mint per friction-review |

## Anti-patterns

| Bad | Good |
|---|---|
| Fable answer on disk, no `derived_from` | S5 attach ≺ G1 skip claim |
| Spawn conductor before S4a | S4a first |
| Rewrite `/work-item-seed` into a liaison drop-list | Seed stays spawn; conductor fault → operator names the lane to the sitting liaison |
| Halt S4a until Fable harvest | Mode B = Fable-before-S4b; identity always fires |
| Feature vs bug as two workflows | One path; kind attribute |
| Copy rich-seed field lists into this skill | Point `/todo` + rich-seed decision |
| Spawn on backlog | Park until promote + re-enter |
| Non-code Q on this path | Divert `/path-sim` at S1 |
| Skip S2 on a non-feature friction because the friction names the exact locus/fix | § Friction-sourced override — S2 fires anyway; skip only via explicit `recon_waived` |
| Stamp `derived_from_friction: "a:{id}"` and expect todo-done to close the friction | `spawned_by_friction=<int>` — that is the auto-`friction_close` key |
| Bare `/work-item-seed` with no idea, or mint before stage table | Halt — need idea text; publish disposition |
| S3 fired ∧ no Fable harvest → S4b / claim G1 skip | Harvest consult first; S4a already done |
| Conductor announces Mode B then ends CHECKPOINT with no admit | Same-turn admit (`execution_id`+`poll_hint` or followup admit) or named halt |
| Mode B then wrapper dies without harvest/handoff (`CONSULT_PENDING` as session-end) | `CONSULT_PENDING` is a wait token — wait for `archive_uri` / `from=web-anthropic`, or honest `partial:consult` harvest-handoff (`NEXT_ADMIT`) |
| Fable/Opus Mode B with only judgment skills (no `ulg-architecture`) | `purpose=ask` — staging owns the arch-pair floor |
| Reconstruct the Fable generate from memory / path-sim Q | Copy § S3 Fable generate recipe (`purpose=ask`; do not rebuild `skills=`) |
| Mode B under live operator-proxy CSE via fresh `team_dispatch(cdp/…)` | `cse_session(op=followup)` into attached CSE (inv 23); fresh CDP only if no live CSE |
| Existing todo + Mode B ask → remint or bare `/layer` | Re-admit conductor · attach · G2 |
| Slash `/work-item-seed {idea}` after same-session NL punch of that idea | S0 hit · re-admit or halt · ¬ second todo · ¬ second conductor |
| Conductor chooses `/path-sim` vs `/layer` as a second admit | Front door already bound: codework = conductor (G-rows *are* layering) |
| S2 breadth via Composer or in-seat Grep spray | Explore subagent first (cheap-recon Tier-1) |
| S2 residual routed to Grok instead of CDP / S3 | Judgment residual after Explore → CDP / S3 (a:31995) |
| S3 fork needs checkout depth → park on the operator for premium approval | Four-condition trigger holds ⇒ fire hop 4 and announce; effort is the card |
| Premium S3 bind ratified by the seat that commissioned it | Independent check is mandatory (terra/Fable) — see the chain SOT |

## Commissioning register (operator bind 2026-08-02)

This path is the **IDE identity mint** before spawn. After S4a the **conductor** owns
S2/S3/S4b/S5 and the G-ladder — Explore (S2), Fable (G1 / Mode B), Composer densify /
G5 implement. `¬` a second admit machine. `¬` a hard rule that the conductor-as-sub-PM
must re-run S1–S6 in-seat after spawn.

`commission(idea) ≻ commission(decomposition)` — the commissioner's job is **enablement**:

| Field on the commission | Why |
|---|---|
| `Use the work-item-seed-path skill` | headless entry surface — ¬ the IDE command |
| kind: `feature-add` \| `investigate+fix` | sets the S2 default (investigate+fix is recon-on). Commission prose — AutoJob admit **defers** `kind:`, it does not bind it. |
| known anchors / loci | lets S2 be legitimately **skipped** ¬ re-derived |
| S3 Mode B mandatory? | if yes, conductor CHECKPOINT owns admit-proof (same-turn admit or halt) |
| expected S6 = spawn conductor | receipt quotes `dispatch_id` + scoreboard URI + Lane B |

Cadence: fewer, fatter commissions amortize round-trip latency vs. paying it per
micro-step. **Life seats** commission via `agent_bus.request` with `desired_model` and
`desired_effort` on the **wire** — do not pin effort in the DIRECTIVE body
(`effort_pin_refused`). **Code-side** `team_dispatch` shape: `seat=cursor-sdk` · omit `model=` ·
`model_knobs={"fast":"true"}` on the dispatch
wire (catalog default is **`fast=true`**; `fast` has no wire param on `agent_bus.request`;
`reasoning_effort` is rejected 422 on `seat=cursor-sdk`). Operator-proxy SOT:
`libs/claude_bundles/operator_proxy_mission.py` § Knob relay.

## Entry surfaces

| Surface | Form |
|---|---|
| Cursor IDE | `/work-item-seed …` (thin command) |
| Headless / Auto / CDP | Use the `work-item-seed-path` skill |
| Commissioned conductor | `agent_bus.request` (life) or `team_dispatch` (code) — see § Commissioning register |

```
/work-item-seed {idea}
/work-item-seed friction a:{id}
/work-item-seed mode=B {idea}
```
