---
name: conductor
description: "On cursor-sdk as mission operator of a continuity root — author/admit a conductor packet, drive G-rows via nested legs, cost-aware model tier, interactive /conductor setup."
lifecycle: active
skill_category: orchestration
trigger_match_terms:
  - conductor
  - mission conductor
  - off-tick conductor
  - cursor-sdk conductor
  - conductor packet
  - conductor score
  - /conductor
  - follow up
  - page me when done
  - I'm leaving
related_skills:
  - orchestration-lanes
  - mission-operator
  - checkpoint-discipline
  - handoff-packet-authoring
  - consult-routing
  - bind-then-compose-dispatch
  - reasoning-posture
  - pager-notify
  - ulg-for-llms
  - judgment-escalation-ladder
  - life-operator-do-chain
---

# Conductor — cursor-sdk as mission operator

**Conductor** = the cursor-sdk seat that **operates** a continuity root (or its
work child) **to completion**: owns the scoreboard, nests specialist legs,
lands its own verified work, and pages the human only for true operator-only
gates. This is how models finish together on one graph after the lid closes
(skill `ulg-for-llms`).

**Do-chain vocabulary** (hop product SOT: `life-operator-do-chain`):

| Term | Means |
|---|---|
| **conductor score** | packet + scoreboard — Mission Composer product; what this seat plays |
| **conductor packet** | six-block admit file (`packet_path=`) |
| **scoreboard** | G-row table the packet binds |

¬ admit package. ¬ shorten conductor score to **score**. Mission Composer ≠ `cursor/composer-2.5`.

**Default is run, don't ask.** Once admitted, the conductor drives every open
G-row through to the scoreboard's own completion criterion in one continuous
commission, and lands its own verified Lane-B branch without a second merge
ask — the admit is standing authorization for both (§ Run to completion).

Not a model name. Not `mission-operator` (that skill is the formal
`operator_proxy` DIRECTIVE/CLOSEOUT grammar, often CDP/life). Conductor is the
**IDE/cursor-sdk off-tick drive** pattern dogfooded on rings 7286 and 7310.

## Audience (binding)

| Seat | Duty |
|---|---|
| **Continuity lead** (IDE / `/conductor`) | Read this skill to author/admit; **require** it on the conductor dispatch (below). **Harvest reader** of any after-ship overlay the conductor fires — quote the sidecar or name why unread |
| **Liaison** (IDE continuity lead when not the conductor) | **Default register** = liaison register (BINDING — operator 2026-08-27). Speak so the human does not need the bus, scoreboard, `SCORE_RESURFACE`, or closeout open. Gloss IDs only when they change what he does next. Skill `audience-register` plain half is the closest written match; the duty name is **liaison register**, not that skill. Off when this seat *is* the conductor. **Decide** = § Liaison-decide |
| **Conductor** (cursor-sdk) | Load this skill on pickup — nest, tier, scoreboard, ¬ hand-code any G-row whose remainder is files+tests after a pick |

### Liaison-decide (binding)

Operator direction ≠ hop recipe. Anticipate harvested score / `NEXT_ADMIT: none` /
spawn-stale materialize / in-flight orphan; choose the next useful move. Inform;
do not wait for a rewritten recipe.

**Decide-before-admit:** Use the `reasoning-posture` skill — pin Question / OOS /
detent. Question is whether the leftover score is still the remit. Resident
alwaysApply does **not** substitute for this cue.

**Named hop vs harvested state:** `runbook:extraordinary-aperture`
(`cortex://notes/runbooks/extraordinary-aperture.md`) — in-seat widen that can
kill the conventional “replay that hop” frame. ¬ `/path-sim` cascade. ¬ remint
a harvested conductor. ¬ leftover-execute.

**Rematerialize trap:** `packet_kind=conductor` + `source_ref=todo:X` forbids
`packet_path` and rematerializes the **old** conductor packet. Harvested score /
`NEXT_ADMIT: none` ⇒ park remints. New remit ⇒ **new sibling todo** +
`contract=implement` (Composer). Never replay the harvested conductor todo.
W5 (`reuse_thread` + same `source_ref`) is unfinished-conductor only.

**After land (binding):** prompt go-live for each serving process that loaded
those paths (hub GIW vs satellite — name the process and what “live” means).
OR if this liaison chooses not to recycle: announce skip in the same turn —
process, why skipped, what stays landed-not-live. When the land was a named
`todo:`: stamp that entity (`todo-close` or LANDED assertion) in the same
turn — recycle without stamp leaves the next resume walking an open card.
Code-live ≠ trading-live (recycle claudeburst ≠ activate
`LIGHTER_LIVE_TRADING`). Continuity `status` LAND-LIVE names **skipped
recycles** and **unstamped matter entities**, not only “not live.”

### Follow up (operator phrases)

Joint convention — the human-visible box lives on the continuity card
(`## Follow up`). Any one phrase is enough. Distinct from page-on-stall and
from CHECKPOINT (reconstitution index, not a follow-up ask).

| Phrase | Liaison |
|---|---|
| `follow up` | Harvest what's terminal. One Been / Are / Going paragraph in the summoning IDE chat. ¬ new hop unless the last bind already said to admit. |
| `follow up on the pager` · `page me when done` · `I'm leaving — follow up` | Same harvest + Use the `pager-notify` skill (awareness; ¬ `COME TO IDE` unless they said come to IDE). Aligns to that skill's “ping me when X”. |
| `status` | **HOPS / BLOCKERS / PENDING / SCORES / BREAKS / LAND-LIVE**. PENDING = leftovers this liaison already named or still dirty on this arc (Mission/resume WIP, in-seat files, landed-not-live, branch-debt). Land-then-live is the default sequence to report. After any land: prompt recycle of each serving process, or announce skip (process · why · landed-not-live). LAND-LIVE names skipped recycles, not only “not live.” Recon-spawn fills the continuity `## Scores / breaks / land-live` box — ¬ dump tables into this skill. ¬ decide-and-admit. |

Named subject (`follow up when hedges land`) is the trigger. Hops still in
flight → say so; do not invent done. `follow up` ≠ remint conductor ≠
leftover-execute. Pin Question (`reasoning-posture`) then report.

**Identity (operator-facing):** never a bare `Qn` / `Gn` / hop id **or bare
bus thread / child-lane number**. One-line identity: what question or score
or hop, whose thread as `{id} {slug}` (`thread_get`). Same for G7 (never a
commissioned live-arm row unless the scoreboard says so) and hop numbers.

**Continuity-lead required-skill gate (BINDING):** before
`team_dispatch(op=generate, seat=cursor-sdk, …)` for a conductor packet, the
lead MUST put `conductor` on the dispatch as a required skill:

1. Packet `<invariants>` MUST include a line:
   `Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost tier from this skill.`
2. Treat slug `conductor` as `required_skills` for the admit (same catalog duty as
   Gate-2 — catalog-registered in `config/skills.yaml`).

`team_dispatch(skills=[…])` is **not** mounted on cursor-sdk (`skills=` skipped
when `backend_type=cursor_sdk`). Do **not** rely on `skills=["conductor"]` alone —
the packet Use-line is the engagement channel. Reasoning-posture may still be
auto-prepended by GIW for `light-bounded`; that does **not** substitute for
`conductor`.

## Invariant

```
conductor(root) ⇒
  packet(six_block) ∧ seat=cursor-sdk ∧ cost_aware_model_tier
  ∧ nest(specialists) ∧ ¬page_human(ranking)
  ∧ drive(all_open_G_rows → completion) ∧ ¬pause_for_interim_continue_ack
  ∧ land(own_verified_lane_B_branch) ⇐ admit_is_standing_merge_ack
  ∧ needs_attended ⇔ operator_only_gate
  ∧ checkout(lane_B) unless named(lane_A_reason)
  ∧ premium_conductor ⇒ announce(why)  # inform-then-proceed; ¬ default
```

## Run to completion (binding default)

The packet admit is a **standing** authorization for the whole mission, not a
per-G-row one. Default posture once running:

- **¬ pause between G-rows for a "continue?" ack.** Drive from the first OPEN
  G-row to the last in one continuous commission. CHECKPOINT is a progress
  report, not a waypoint that blocks on a reply before the next G-row starts.
- **¬ a second gate on the mission's own merge.** `git-posture` gates
  `git_land` / `git_integrate` on "operator directs a merge" — for a conductor
  mission, admitting the packet **is** that direction, standing for the
  mission's own Lane-B branch. Land on green (tests pass, AC met) as part of
  *completion*; do not round-trip for a separate "ok to merge?"
- **Stop only for a true operator-only gate** — credentials, an irreversible
  non-revertible act, or a genuine unranked fork. Forks go to the judgment
  ladder (independent binder — Fable / Opus / terra) first; `needs-attended`
  is for the human-only remainder, not for "should I proceed" or "should I
  merge."
- **Named exception overrides the default.** If a mission genuinely needs the
  merge held for review (destructive scope, force-push, cross-repo blast
  radius), name that in the packet `<invariants>` — silence means rubber-
  stamped, not the other way around.
- **Flagging is not a hold.** Judging a mission large, doctrine-touching, or
  risky and wanting to "lay out the plan first" is not a named exception —
  it is commentary. Nest Composer and drive every G-row to green; note the
  concern on the CHECKPOINT while the work proceeds. Stopping before any
  G-row starts — zero files touched, nothing nested — is the same violation
  as skipping straight to a merge ask (refuse-and-close, incident 7419;
  distinct from absorb, incident 7407, which hand-codes instead of nesting).
- **After-ship `cdp/opus-5` session/work review is recommended, not a hold.**
  On green land, fire `team_dispatch(model=cdp/opus-5, purpose=review, reasoning_effort="high")` in
  the background (`consult-routing` § CDP transport). Latency is not a skip
  on this seat. Defer only when the harvest would block the next *attended*
  move — and name the deferral. Does not replace path-sim R-after (Grok).
  **Scoreboard default:** Mission Composer comments this overlay on the
  scoreboard at mint (Sidecars / WIP — **¬** a gated G-row; done-claim must
  not wait on it). Template: `cortex://notes/system/templates/charter-scoreboard.md`.
  **Reader (BINDING):** `fired(overlay) ⇒ reader = summoning-thread lead at harvest`.
  `¬wait(latency) ≠ ¬read`. Harvest MUST quote overlay `read_sha256` ∨ name why unread.
  A check with no reader is not an independent check (dogfood 9655 / assertion 30663).

## When

Fire when **any**:
- Operator wants cursor-sdk to drive an open G-row scoreboard end-to-end
- Continuity root exists (`role:root` + charter + scoreboard) and Next-pickup is
  multi-leg (investigate → disposition → conditional implement)
- `/conductor` interactive setup completes and operator says admit

Anti-trigger: single dense `source_ref` implement (use `/todo` / wrap); formal
CDP `operator_proxy` mission lane (use `mission-operator` + `cdp-operator-proxy`);
attended lead staying through the whole arc (use `orchestrator-workflow` P1–P5 —
P1 decompose, P2 fan-out, P3 fork protocol, P4 adjudicate, P5 close-back — instead
of a conductor dispatch).

## Model / effort tier (cost-aware — binding)

`cursor/claude-opus-5` is **expensive**. Prefer the **cheapest tier that can
honestly hold the conductor remit**. Re-check when pricing or fleet defaults
move (`observability` dispatch-economics when spend matters). Compose with
`lean-context-dispatch-first` + `consult-routing` — non-primary models stay
operator-gated unless a standing rule names them.

**Pool first, then rate.** Grok and Composer draw Cursor Models (generous).
Sonnet / Opus / Terra draw the capped Other Models (second) pool. T1=Grok
is a **quota/allowance-scarcity** call, not a verified per-token savings:
Sonnet is the scarce Ultra allowance on a capped pool; Grok is not.
Do not cite total-dollar-by-model spend as the justification when call
volumes differ — reprice the **same token mix** at both rate cards first.
Cache-read-heavy workloads can make Sonnet cheaper per token (`cache_read`
`$0.2/M` vs Grok `$0.5/M`; input both `$2/M`; output `$10/M` vs `$6/M`).
Rates: `config/model_rates.yaml`.

| Tier | Default conductor model | Effort | Use when |
|---|---|---|---|
| **T0 — mechanical drive** | omit `model=` → `cursor/composer-2.5` | (n/a) | Scoreboard fully bound; only nest Composer/investigate; conductor is traffic cop |
| **T1 — default judgment** | **`cursor/grok-4.6`** | **`xhigh`** | **Standing default.** Multi-G orchestrate, rank, adjudicate — Cursor Models pool. Omit `fast` unless an arc pin **or the summoning continuity-doc `## Rules`** names it (card omit-path). Nested T1 judgment inherits when that house names T1 fast. |
| **T2 — Other Models** | `cursor/claude-sonnet-5` | `xhigh`/`max` (`thinking=true`, `context=1m`) | Named trigger only — grok cannot hold the remit or the context window. **Explicit pin holds its card ceiling even unattended** — the pool cap only ever bites the *silent default* path (`resolve_desired_model`'s omit branch never resolves to Other Models for any contract), never a deliberate T2/T3 pick |
| **T3 — premium** | `cursor/claude-opus-5` | full card (`low`→`max`) | Invariant-touching, architecture-suitability, ≥2 unranked co-primaries, recurrence — **inform-then-proceed** + one-line why (trigger is *whether to pick T3*, not the effort rung) |

Terra is **not** a standing conductor tier (Other Models + mid GPT rate). Cross-family binder stays on `judgment-escalation-ladder` 2c, not the default conductor seat.

**1M context is the T1→T2 trigger, not effort alone (operator bind 2026-08-18):** Grok-4.6's card has no `context` knob and tops out at `xhigh` — there is no `max` rung on Grok. When a G-row genuinely needs the wider window, that is itself the named trigger to pick T2 Sonnet-5 at `xhigh`/`max`/`context=1m`, attended or not.

**Nested legs (always split by cost class):**
- Mechanical implement → Composer (`omit model=`, `contract=implement`)
- Investigate densify → usually T1 (Grok @ `xhigh`); escalate T2/T3 only on open judgment forks
- Independent binder when conductor unsure → ladder (`judgment-escalation-ladder`); ¬ burn Opus to rubber-stamp its own bind

**Anti-patterns (cost):**
| Bad | Good |
|---|---|
| Default every conductor to Opus or Sonnet `max`/`1m` | T1 Grok @ `xhigh`; Other Models only on a named trigger |
| Premium model at default effort | Cheaper model at high effort **on the same pool** |
| T1/T3 conductor that also hand-codes a mechanical remainder after a pick | Nest Composer |
| Re-spend Opus to amend a densified packet | Composer / T1 amend |
| Ignore `sdk_cost_risk` warning | Downgrade model or split bind/compose |
| Pin Terra/Sonnet because the skill used to | Cursor Models T1 unless the remit actually needs Other Models |
| Cite total-dollar-by-model as T1 justification when call volumes differ by an order of magnitude | Reprice the same token mix at both rate cards; justify T1 by quota/allowance scarcity, not price |

`/conductor` asks model tier (Q8) when unbound; operator may pin a slug.

## Role split

| Seat | Does |
|---|---|
| **Conductor** (tier from table, `light-bounded`) | Orient, rank, update scoreboard/CHECKPOINT, nest legs, adjudicate closeouts. Bind the token/locus; ¬ implement. `light-bounded` / `owner: cursor-sdk` ⇏ conductor writes files+tests |
| Nested **investigate** | Forensic / AC bind — pick tier by judgment density |
| Nested **Composer** `contract=implement` | Mechanical G-row after densify; `nest_under` when lease held |
| Independent binder | Ladder step-2 when conductor unsure (weight/family) — ¬ self-ratify |
| Human | Credentials, kill tabs, irreversible acts — `needs-attended` + one recommended answer. **¬** interim "continue?" or "ok to merge?" — both rubber-stamped by the admit (§ Run to completion) |

## Packet

Six-block handoff packet (`architecture-handoff-protocol`). Front-matter SHOULD
set `packet_kind: conductor` and `role_name: conductor`.

Required in `<scope>` / `<invariants>`:
- **`Use the conductor skill — …`** (continuity-lead required-skill gate — see Audience)
- Root thread id + charter + scoreboard URIs
- Checkout regime: **Lane B is the standing default** — state it explicitly
  (`lane="B"`; an *omitted* `lane=` still resolves to Lane A at GIW, so
  default ≠ "leave the param off"). Lane A only on a named reason (trivial
  single-locus mechanical, or scope genuinely incompatible with a worktree)
- Incident/sibling lanes (cite ≠ convert)
- Forbidden verbs (e.g. no `request` on a stood-down lane)
- Judgment vs human rule (above)
- **Run-to-completion restated** — state plainly that this admit already
  authorizes landing the mission's own Lane-B branch on green; no
  plan-review or merge-ack round trip is needed unless *this* packet names a
  specific hold-merge exception in this same list
- **Bound conductor model + effort** (or "lead picks at admit from tier table")
- **G-row contract honesty** — do not mark a G-row light-bounded-direct / `owner: cursor-sdk` when `files_expected` includes production code+tests. Conductor binds; Composer implements.
- **Scoreboard overlay (good default)** — after the last landed code G-row,
  comment `cdp/opus-5` `purpose=review` `reasoning_effort="high"` of the landed diff as recommended
  background review. Record fire or named deferral in Sidecars / WIP.
  **¬** mint it as a gated G-row (done-claim must not wait on it).

Continuity sidecar during run: `cortex://notes/system/threads/{root}-conductor.md`
(G-row table, nested `execution_id`s, `NEXT_ADMIT`, judgment calls) — same shape
as `7286-off-tick-conductor.md`.

Worked packet example: `tmp/reviews/7310-conductor-packet.md` /
`cortex://notes/system/threads/7310-conductor-packet.md` (early dogfood used
Opus — not the standing default).

### First-utterance spawn (standing path)

IDE mints todo identity (S4a); Stargate materializes the packet:

```text
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  contract=light-bounded,
  lane="B",
  source_ref="todo:{slug}",
  packet_kind="conductor",
  dispatch_thread_id="{root}",   # continuity root with turns, or pending-empty work child
)
```

First-utterance **mint** only. Harvested score / `NEXT_ADMIT: none` is the
rematerialize trap (§ Liaison-decide), not this recipe.

`{root}` = continuity root that already has turns, **or** a
`lifecycle_state=pending` ∧ `turn_count==0` child of that root. Lifecycle-null
pre-create 422s (`conductor_coord_split_refused`). Resume-after-terminal:
`reuse_thread=<work thread>`. Receipt quotes the **admitted** thread +
`branch_current=cursor-sdk/lane-{that id}` + `dispatch_id` + `scoreboard_uri`.
Ledger holds
`work_key=todo:{slug}` (no `todo:` packet front-matter — nested G5 uses
`nest_under`). Top-level `contract=implement` on the same todo while conductor
is open → 409.

### Score journal + stops

- Tip: `cortex://notes/system/scoreboards/{slug}-scoreboard.md`
- Journal: `cortex://notes/system/scoreboards/{slug}-score-journal.md` (append-only)
- **Witnessed DONE:** Status cells are a **projection** folded from witnesses (cortex
  relationships, bus turns, git land). Nobody writes `DONE` — hang witnesses; the fold
  renders `DONE`. Self-marked `DONE` without a witness renders **`CLAIMED`** (not a
  rewind of a closed row).
- **Conductor duty:** attach witnesses (G1 `derived_from` edge, G5 `SCORE_RESURFACE`
  when attended, etc.); do not re-derive work already in sidecar artifacts.
- **G5 ≠ attended-door only.** `SCORE_RESURFACE` witnesses the attended resurface,
  ¬ implement completeness. `G4.withhold ∨ G4.AC_red ∨ G4.says(remainder is mechanical)
  ⇒ ¬ close G5` on Composer land ∧ empty-template green. Hang G5 until a *read*
  independent check (overlay quoted at harvest) ∨ a seeded-ladder fixture witnesses
  the named remainder. Fold: a G4 URI whose body withholds/FAIL G5 is **not** a
  G4 witness (v1 URI-resolve alone was the 9655 collapse).
- **`SCORE_RESURFACE` thread:** post on `summoning_thread_id` (parent/root —
  9582/9638-class), **never** the leftover worker thread. Packet scope names
  `summoning_thread_id:`; GIW attended preamble repeats it.
- Stops:
  - **Wait (do not terminate):** `CONSULT_PENDING` — harvest → document →
    `derived_from` → next row. Honest wrapper is `partial:consult` +
    `NEXT_ADMIT`, never `gate_d` / `work`.
  - **Exit-and-persist:** `ROW_PINNED` · `HOLD_MERGE` · `OPERATOR_GATE` ·
    `PARKED_TRANSPORT` — persist, then exit. `ROW_PINNED` after honest
    `SCORE_RESURFACE` on the summoning thread is `partial:consult`, not work
    failure.
  - Also: `CONFIRM_PENDING` · `DONE` (stop token only — not row Status)
- **`CONSULT_PENDING` wait:** the generate session waits or hands off — it does
  not end. `agent_bus.wait` until `archive_uri` or `from=web-anthropic` harvest
  turn; chrome-only continues wait (bounded under remaining wall).
- G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal).
  Explicit see-score → `ROW_PINNED` + ping.
- Attended IDE spawn: resurface the score in the summoning chat at G3→G5 unless
  the summon named confer-and-finish.
- `cursor-auto` / no live summoning chat = confer-and-finish (Q2 unchanged).
- `ROW_PINNED` / stall / QWA pages the operator when away, when see-score is
  explicit, **or** when the summoning IDE is liaison (human not in that chat).
  No pager only when the live summoning chat **is the human operator**.
  Liaison IDE ≠ operator-present. Bus `Quiet with work in flight` is not a page
  (`qwa-*` on the worker thread → `to=cursor` is the named miss; 9638#187).
- Mode B admit-proof on CHECKPOINT when `CONSULT_PENDING`: `execution_id`+
  `poll_hint` or honest halt.

### Resume-if-dead (binding)

`CONSULT_PENDING` is **not** a designed-stop terminate. Exit-and-persist stops
(`ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE`, `PARKED_TRANSPORT`) retain store +
worktree (`resume_retain`).

**Never** `team_dispatch(..., resume_of=...)`. `resume_of` is a GIW worker-POST
field only (a:30793) — team_dispatch 422s it.

If the worker thread is **still live**: do not second-generate on it —
`422 CURSOR_WORKER_THREAD_OCCUPIED`. Poll or `nest_under` the live holder.

If the worker is **terminal**, mint a **new** top-level conductor (new
`dispatch_id`). Nest Composer under **that** id, not a ghost parent.

**Unused legal resume is a feature gap (BINDING — operator 2026-08-27):**
`ROW_PINNED` ∧ worker terminal ∧ `reuse_thread=<that worker>` is legal ∧
summoning chat still live ⇒ the conjurer **fires that reuse this session**.
Park-for-later sibling, “CI first then maybe resume”, or minting a new work
thread while the pinned worker is reusable = the defect, not patience.
New `dispatch_id` is required (dead execution ≠ dead thread). `resume_of=`
stays illegal. House folds (pager, nest-orphan, scoreboard) ride **on** that
resume — they do not postpone it.

**Resume incompleteness (named gap — operator 2026-08-27):** `reuse_thread`
is **not** SDK resume. It admits a **new** generate on the same bus thread.
GIW `resume_of` continues the **same** Cursor agent (`resume_agent` + retained
store/worktree). That field is worker-POST only; `team_dispatch` has no
`resume_of` (schema `additionalProperties: false`; a:30793 422). `/conductor`
has no resume step. Attended `ROW_PINNED` **exits** the generate (W3), so
pin-then-continue is never the same stream. Do not flatten: unused legal
`reuse_thread` (practice) ≠ missing conjurer `resume_of` (product).

```text
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  contract=light-bounded,
  packet_kind=conductor,
  source_ref=todo:{slug},
  reuse_thread=<terminal work thread>,   # omit to mint a sibling worker
  dispatch_thread_id=<parent root>,      # SCORE_RESURFACE + coord target
  lane="B",
  # confer-and-finish without prompt+source_ref 422:
  #   generation_options={summon_mode: confer_and_finish}
  #   or todo attr summon_mode=confer_and_finish
)
```

**Packet carry is mandatory.** GIW preambles extract from **packet text only**.
Re-admit with `source_ref` materialize (or the same packet + Use-line) only
when the conductor is **unfinished**. Harvested / `NEXT_ADMIT: none` is not
resume — rematerialize trap (§ Liaison-decide).

**Identity rewrite (binding on the new seat):**

| Artifact | After re-admit, name the **new** `dispatch_id` |
|---|---|
| Sidecar `NEXT_ADMIT` / nested Composer | `nest_under=<new dispatch_id>` |
| Seat-identity preamble | new `dispatch_id` supersedes the dead parent |
| Scoreboard tip (when present) | rewrite `NEXT_ADMIT` nest target to the new id |

Pager only when `summon_mode` is absent/away or the packet names see-score-page —
not on every attended `ROW_PINNED` (attended floor = bus `SCORE_RESURFACE` on the
summoning thread + summoning lead relay).

## Admit

```text
# Default judgment conductor (T1) — prefer unless tier table says otherwise
# Precondition: packet <invariants> already carries "Use the conductor skill — …"
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  model=cursor/claude-sonnet-5,   # omit for T0; terra/opus only per tier
  contract=light-bounded,
  packet_path=tmp/reviews/{slug}-conductor-packet.md,
  dispatch_thread_id={root},      # continuity root with turns, or pending-empty child of root
  # generation_options={summon_mode: confer_and_finish},  # optional; or todo attr
  model_knobs={effort: max, thinking: "true", context: "1m"},
  lane="B",                       # DEFAULT — always pass explicitly. SOT:
                                   # consult-routing § cursor-sdk checkout lane.
                                   # omit = inherit only (nest/resume), ¬ preference.
                                   # lane="A" only on a named reason.
  # skills=["conductor"]  # optional document; ¬ mounted on cursor-sdk — Use-line wins
)
```

Preflight: packet Use-line present ∧ `manage(busy_status)` — if the chosen lane's
write lease is held by another dispatch, expect **queued**; record holder on the
root CHECKPOINT. ¬ nest_under an unrelated mission's lease.

**Post-admit check (binding — default regime is Lane B):** quote `active_by_lane` /
`holder_source_repo` from `busy_status`. Expected: `B≥1`, worktree under
`ulg-arc-worktrees/lane-*`, branch `cursor-sdk/lane-*`. Substrate **refuses**
`lane="B"` without a materialized worktree (`422 CURSOR_LANE_B_WORKTREE_MISSING`)
instead of silently admitting on shared master. If you see `A=1` and
`holder_source_repo=…/universal-llm-gateway` without a named Lane-A reason, the
admit selected Lane A (omitted/`lane="A"`) — stop nesting mechanical work onto
a B branch that isn't this checkout.

When admitting **T3 Opus**: one announce line (`Conductor T3: <trigger> — <why>`),
then proceed (`lean-context-dispatch-first` inform-then-proceed).

## Gotchas

### When Lane A is still the right call

Lane B is the default, not the only option. Name Lane A explicitly, one line
in packet `<invariants>`, when **either**: the mission is T0-mechanical — a
single locus, self-contained, nothing else plausibly touching that file mid-
mission — **or** the scope is structurally incompatible with a worktree (an
absolute mount path or non-repo URI that `CURSOR_LANE_B_SCOPE_REFUSED` cannot
be resolved for, below). Absent a named reason, admit Lane B.

### `CURSOR_LANE_B_WORKTREE_MISSING` — B without a tree is not A

`lane="B"` always requires a minted or inherited worktree. Nesting
`lane="B"` under a Lane-A parent (shared-master lease) is **422**, not a
relabel. Omit `lane=` on a nest to inherit the parent's isolation; pass
`lane="A"` only with a named reason. Do not set `CURSOR_SDK_REFUSE_B_WITHOUT_WORKTREE` —
refusal is unconditional.

### `CURSOR_LANE_B_SCOPE_REFUSED` ≠ license to omit `lane=`

**Failure class (7281 / 7286, 2026-08-15):** Operator-bound Lane B. Explicit
`lane="B"` refused with `CURSOR_LANE_B_SCOPE_REFUSED` ("files_expected contains
paths outside source_repo"). Lead retried **without** `lane=` so the conductor
would admit. GIW defaulted to **Lane A** (shared master write lease). Meanwhile
the G3 Composer candidate sat on `cursor-sdk/lane-7312`. Result: conductor and
implementation on **two checkouts** — master vs working branch split.

| Wrong | Right |
|---|---|
| Omit `lane=` to "get past" the refusal | Fix the packet / derived `files_expected` so every path is repo-relative under `source_repo`, then re-admit with `lane="B"` |
| Proceed after admit without reading `active_by_lane` | Confirm Lane B worktree before nesting Composer or editing |
| Land by `git checkout <branch> -- <paths>` onto master | Merge the lane branch via `git_land` (see `git-posture`) — the mission's own admit is the standing merge ack; land on green, don't re-ask |

Scope refuse usually means an absolute mount path, `cortex://` / non-repo URI, or
a path outside the gateway checkout leaked into machine-derived file scope.
Strip those from the conductor packet (and any `source_ref` materializer inputs)
until `lane="B"` admits cleanly.

Nested Composer under a Lane-B conductor must `nest_under=<conductor dispatch_id>`
**and** inherit Lane B — ¬ fire a fresh top-level implement that can mint another
branch or fall onto master.

### Self-nest routing — seat-identity preamble exposes your `dispatch_id`

Lane-B `light-bounded` conductor missions receive a **seat-identity preamble** from
GIW `resolve_prompt_preamble` when the dispatch is a genuine conductor packet:
either **`packet_path`** is set (IDE `team_dispatch`) **or** the body carries the
mandatory literal line ``Use the conductor skill — …`` (message-body
``COMMISSION_CONDUCTOR`` via `cursor_request` → cursor-auto — no `packet_path`).
The preamble names your own GIW **`dispatch_id`** (short ledger id — **not** the
Stargate execution UUID on bus `to:`) and the two nesting paths:

| Path | When | Shape |
|---|---|---|
| **(a) Independent dispatch** | Judgment/spec-only work that will **not** land on this mission's branch (investigate, confer, dense spec bind) | Separate `team_dispatch` **without** `nest_under` |
| **(b) Mechanical landing** | G-rows whose code must merge on this mission branch | `team_dispatch(..., nest_under=<your dispatch_id>)` so the child inherits Lane B |

**Never** substitute independent dispatch for `nest_under` on mechanical landing work —
that mints another branch or falls onto master. Historical class: conductor absorbs
~1k+ SLOC in-seat (7407) when it cannot discover its own id.

Nested implement packets: omit `todo:` front-matter when the parent already holds that
work-identity — repeating it 409s `CURSOR_SOURCE_REF_IN_FLIGHT`.

### Refuse-and-close != caution — flag it, still execute

**Failure class (7419, 2026-08-16 — friction 29694/29693):** a T1 conductor
read the full mission (28 read-only tool calls, zero files touched) then
closed `status: partial` / `work_outcome: checks_failed` because, in its own
words, it wanted to "lay out the concrete plan and flag that merge step
explicitly before doing anything, rather than just executing straight
through." The packet named no hold-merge exception — § Run to completion
already grants standing authorization for exactly that merge. The conductor
invented an exception nobody named, and made zero progress while inventing
it: it neither nested Composer nor touched the mechanical G-rows leading up
to the flagged step.

The seat-identity preamble now restates this at the exact point it exposes
the conductor's own `dispatch_id` (`_CONDUCTOR_RUN_TO_COMPLETION_TEMPLATE` in
`cursor_sdk_packet.py`) — the reminder is in-context at dispatch time, not
only in this skill file. Further fixes fold under
`task:conductor-self-nest-routing` — do not mint a parallel todo.

## Interactive entry

Command `/conductor` (plugin): orient → ask establishing questions (incl. **model
tier**; checkout regime pre-filled **Lane B**, confirm or override to Lane A) →
draft charter/scoreboard/packet → confirm → admit. Skill body does not re-ask
when the operator already bound the answers in chat. Scoreboard mint includes
the after-ship `cdp/opus-5` review comment (good default; ¬ a G-row).

## Composes with

| Skill / rule | Boundary |
|---|---|
| `orchestration-lanes` | Root/mission birth; conductor **runs** after birth |
| `mission-operator` | Formal operator_proxy turn schema — different lane kind |
| `checkpoint-discipline` | Root CHECKPOINT tip hygiene |
| `handoff-packet-authoring` | Six-block authoring |
| `bind-then-compose-dispatch` | Mechanical nest after a pick (any G-row) |
| `judgment-escalation-ladder` | Unsure → binder, not human |
| `git-posture` | Lane-B branch land = merge/`git_land`; ¬ path-copy onto master. Conductor admit = standing "operator directs a merge" for its own branch (§ Run to completion) — ¬ a second gate |
| `lean-context-dispatch-first` | Tier ladder + Opus inform-then-proceed |
| `consult-routing` | Model split / non-primary gate · **cursor-sdk `lane=` caller recipe** (this skill does not own omit semantics) |
| `life-operator-do-chain` | Hop names + products (Sketch → shape bind · Mission Composer → conductor score · this seat plays it) — named hop is direction, not a recipe when harvest conflicts |
| `reasoning-posture` | Liaison decide-before-admit — pin Question / OOS / detent |
| `runbook:extraordinary-aperture` | Named hop vs harvested state — in-seat widen; ¬ path-sim cascade |

## Anti-patterns

| Bad | Good |
|---|---|
| Admit conductor packet without `Use the conductor skill` in `<invariants>` | Continuity-lead required-skill gate (Audience) |
| Rely on `team_dispatch(skills=["conductor"])` alone for cursor-sdk | Packet Use-line — `skills=` is not mounted on cursor-sdk |
| One flat `implement` "does the whole mission" | Conductor + nested contracts per G-row |
| Page human "which remedy?" | Nest binder; `needs-attended` only for operator-only |
| Conductor hand-codes any G-row whose remainder is files+tests after a pick | Nest Composer |
| nest_under sibling mission lease | Queue or wait; record holder |
| Convert incident lane into root mid-flight | Cite incident; root stays continuity |
| Wait forever on sibling merge without bind | Scoreboard cite-only vs explicit wait criterion |
| Drop `lane="B"` after `CURSOR_LANE_B_SCOPE_REFUSED` | Fix scope paths; re-admit with `lane="B"` |
| Treat missing Lane-B worktree as a shared-master admit | Expect `422 CURSOR_LANE_B_WORKTREE_MISSING`; mint/inherit a tree or name Lane A |
| Conductor on Lane A while G-row code is on `cursor-sdk/lane-*` | One regime: conductor + nests share the Lane-B worktree/branch |
| Opus-by-default for every conductor | Tier table; T1 Grok @ `xhigh`; Opus only with trigger |
| Omit `lane=` assuming that means "no preference" | Lane B is the default — pass `lane="B"` explicitly; name Lane A only with a reason |
| Conductor pauses after a G-row to ask "continue?" | Drive to completion in one commission; report via CHECKPOINT, don't wait for a reply |
| Treat the mission's own `git_land` as a second approval gate | Admit is the standing merge ack; land on green + AC met (§ Run to completion) |
| Escalate "ok to merge?" to the human mid-mission | Land it; escalate only genuinely operator-only acts |
| Conductor judges the mission "too big"/risky and stops before any G-row, unasked — or verifies the mission is genuine then refuses it over a later step's scale (7419) | Nest Composer, drive to green; only a **named** packet exception holds the merge — scale/blast-radius/"verified legitimate" alone are never an implicit one. Execute the current step, raise the concern in the closeout, reassess only at the flagged step under standing authorization (reasoning-posture rule 6 mirror) |
| Closes `status: partial`/`checks_failed` with zero files touched because it wanted to flag the plan first | Flag the concern on the CHECKPOINT while still driving — flagging is commentary, not a hold |
| Independent `team_dispatch` (no `nest_under`) for mechanical G-row landing work | `nest_under=<conductor dispatch_id>` + Composer `contract=implement` — independent dispatch is judgment/spec-only |
| Close G5 because G4 said “remainder is mechanical” + empty-template green | Hang G5; read the overlay or seed a fixture — G4 withhold is not a G5 witness |
| Fire after-ship `cdp/opus-5` review and never read it | Summoning-thread lead quotes the overlay sidecar at harvest, or names why unread |
| Treat named hop / `packet_kind=conductor` + `source_ref=todo:X` as a recipe when the score is harvested / `NEXT_ADMIT: none` | Liaison-decide; park remints; new remit → sibling todo + Composer implement |
| Land then stay silent on recycle (or write LAND-LIVE as only “not live”) | Prompt go-live for each serving process, or announce skip in the same turn; LAND-LIVE names the skipped recycle |
| Land a named `todo:` and leave the card `open` | Stamp `todo-close` / LANDED on that entity in the same turn |
