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
  - /conductor
related_skills:
  - orchestration-lanes
  - mission-operator
  - checkpoint-discipline
  - handoff-packet-authoring
  - consult-routing
  - bind-then-compose-dispatch
  - reasoning-posture
  - judgment-escalation-ladder
---

# Conductor — cursor-sdk as mission operator

**Conductor** = the cursor-sdk seat that **operates** a continuity root (or its
work child) **to completion**: owns the scoreboard, nests specialist legs,
lands its own verified work, and pages the human only for true operator-only
gates.

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
| **Continuity lead** (IDE / `/conductor`) | Read this skill to author/admit; **require** it on the conductor dispatch (below) |
| **Conductor** (cursor-sdk) | Load this skill on pickup — nest, tier, scoreboard, ¬ hand-code G5 |

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

**Cheaper model at higher effort beats a premium model at default effort.** Sonnet 5
carries Opus 5's whole knob surface (`thinking`, `context: 1m`, `effort` through
`max`) at **$2/$10 vs $5/$25** per M tokens — so `max` effort on Sonnet 5 still
costs less than `high` on Opus. Rates: `config/model_rates.yaml`
(live probe 2026-08-15).

| Tier | Default conductor model | Effort | Use when |
|---|---|---|---|
| **T0 — mechanical drive** | omit `model=` → `cursor/composer-2.5` | (n/a) | Scoreboard fully bound; only nest Composer/investigate; conductor is traffic cop |
| **T1 — default judgment** | **`cursor/claude-sonnet-5`** | **`max`** (`thinking=true`, `context=1m`) | **Standing default.** Multi-G orchestrate, rank, adjudicate |
| **T2 — cross-family** | `cursor/gpt-5.6-terra` | `reasoning=max` (¬ `extra-high` — not an accepted value) | Independent check, or T1 unsure. `gpt-5.6-sol` only when scope is narrow (it prices at Opus level) |
| | ↳ set `context=272k` unless 1m is needed — GPT long context bills **2x input**, and the live Terra/Sol default is `1m`. Sonnet 5 has no long-context surcharge, so T1 `1m` is free. | | |
| **T3 — premium** | `cursor/claude-opus-5` | full card (`low`→`max`) | Invariant-touching, architecture-suitability, ≥2 unranked co-primaries, recurrence — **inform-then-proceed** + one-line why (trigger is *whether to pick T3*, not the effort rung) |

**Not conductor seats:** `cursor/grok-4.6` (reliability — cheap breadth/recon only,
and its `$2/$6` was a launch discount).

**Nested legs (always split by cost class):**
- Mechanical implement → Composer (`omit model=`, `contract=implement`)
- Investigate densify → usually T1 (Sonnet 5 @ `max`); escalate T2/T3 only on open judgment forks
- Independent binder when conductor unsure → ladder (`judgment-escalation-ladder`); ¬ burn Opus to rubber-stamp its own bind

**Anti-patterns (cost):**
| Bad | Good |
|---|---|
| Default every conductor to Opus high | T1 Sonnet 5 @ `max`; escalate with named trigger |
| Premium model at default effort | Cheaper model at `max` — same ladder, lower rate |
| Opus conductor that also hand-codes G5 | Nest Composer |
| Re-spend Opus to amend a densified packet | Composer / T1 amend |
| Ignore `sdk_cost_risk` warning | Downgrade model or split bind/compose |
| Grok on a conductor seat | Recon/breadth only |

`/conductor` asks model tier (Q8) when unbound; operator may pin a slug.

## Role split

| Seat | Does |
|---|---|
| **Conductor** (tier from table, `light-bounded`) | Orient, rank, update scoreboard/CHECKPOINT, nest legs, adjudicate closeouts |
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

Continuity sidecar during run: `cortex://notes/system/threads/{root}-conductor.md`
(G-row table, nested `execution_id`s, `NEXT_ADMIT`, judgment calls) — same shape
as `7286-off-tick-conductor.md`.

Worked packet example: `tmp/reviews/7310-conductor-packet.md` /
`cortex://notes/system/threads/7310-conductor-packet.md` (early dogfood used
Opus — not the standing default).

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
  dispatch_thread_id={root},      # work child when G-rows live there (e.g. 7286)
  model_knobs={effort: max, thinking: "true", context: "1m"},
  lane="B",                       # DEFAULT — always pass explicitly; an omitted
                                   # lane= resolves to Lane A at GIW, not "no
                                   # preference". lane="A" only on a named reason
                                   # (see Packet § Checkout regime).
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
when the operator already bound the answers in chat.

## Composes with

| Skill / rule | Boundary |
|---|---|
| `orchestration-lanes` | Root/mission birth; conductor **runs** after birth |
| `mission-operator` | Formal operator_proxy turn schema — different lane kind |
| `checkpoint-discipline` | Root CHECKPOINT tip hygiene |
| `handoff-packet-authoring` | Six-block authoring |
| `bind-then-compose-dispatch` | G5 mechanical nest |
| `judgment-escalation-ladder` | Unsure → binder, not human |
| `git-posture` | Lane-B branch land = merge/`git_land`; ¬ path-copy onto master. Conductor admit = standing "operator directs a merge" for its own branch (§ Run to completion) — ¬ a second gate |
| `lean-context-dispatch-first` | Tier ladder + Opus inform-then-proceed |
| `consult-routing` | Model split / non-primary gate |

## Anti-patterns

| Bad | Good |
|---|---|
| Admit conductor packet without `Use the conductor skill` in `<invariants>` | Continuity-lead required-skill gate (Audience) |
| Rely on `team_dispatch(skills=["conductor"])` alone for cursor-sdk | Packet Use-line — `skills=` is not mounted on cursor-sdk |
| One flat `implement` "does the whole mission" | Conductor + nested contracts per G-row |
| Page human "which remedy?" | Nest binder; `needs-attended` only for operator-only |
| Conductor hand-codes G5 | Nest Composer |
| nest_under sibling mission lease | Queue or wait; record holder |
| Convert incident lane into root mid-flight | Cite incident; root stays continuity |
| Wait forever on sibling merge without bind | Scoreboard cite-only vs explicit wait criterion |
| Drop `lane="B"` after `CURSOR_LANE_B_SCOPE_REFUSED` | Fix scope paths; re-admit with `lane="B"` |
| Treat missing Lane-B worktree as a shared-master admit | Expect `422 CURSOR_LANE_B_WORKTREE_MISSING`; mint/inherit a tree or name Lane A |
| Conductor on Lane A while G-row code is on `cursor-sdk/lane-*` | One regime: conductor + nests share the Lane-B worktree/branch |
| Opus-by-default for every conductor | Tier table; T1 Sonnet 5 @ `max` default; Opus only with trigger |
| Omit `lane=` assuming that means "no preference" | Lane B is the default — pass `lane="B"` explicitly; name Lane A only with a reason |
| Conductor pauses after a G-row to ask "continue?" | Drive to completion in one commission; report via CHECKPOINT, don't wait for a reply |
| Treat the mission's own `git_land` as a second approval gate | Admit is the standing merge ack; land on green + AC met (§ Run to completion) |
| Escalate "ok to merge?" to the human mid-mission | Land it; escalate only genuinely operator-only acts |
| Conductor judges the mission "too big" and stops before any G-row, unasked | Nest Composer, drive to green; only a **named** packet exception holds the merge — scale/blast-radius alone is never an implicit one |
| Closes `status: partial`/`checks_failed` with zero files touched because it wanted to flag the plan first | Flag the concern on the CHECKPOINT while still driving — flagging is commentary, not a hold |
| Independent `team_dispatch` (no `nest_under`) for mechanical G-row landing work | `nest_under=<conductor dispatch_id>` + Composer `contract=implement` — independent dispatch is judgment/spec-only |
