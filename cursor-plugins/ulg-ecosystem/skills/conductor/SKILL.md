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
work child): owns the scoreboard, nests specialist legs, and pages the human
only for true operator-only gates.

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
  ∧ needs_attended ⇔ operator_only_gate
  ∧ premium_conductor ⇒ announce(why)  # inform-then-proceed; ¬ default
```

## When

Fire when **any**:
- Operator wants cursor-sdk to drive an open G-row scoreboard end-to-end
- Continuity root exists (`role:root` + charter + scoreboard) and Next-pickup is
  multi-leg (investigate → disposition → conditional implement)
- `/conductor` interactive setup completes and operator says admit

Anti-trigger: single dense `source_ref` implement (use `/todo` / wrap); formal
CDP `operator_proxy` mission lane (use `mission-operator` + `cdp-operator-proxy`).

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
| **T3 — premium** | `cursor/claude-opus-5` | `low`→`high`; `xhigh`/`max` need standing trigger | Invariant-touching, architecture-suitability, ≥2 unranked co-primaries, recurrence — **inform-then-proceed** + one-line why |

**Not conductor seats:** `cursor/grok-4.6` (reliability — cheap breadth/recon only,
and its `$2/$6` was a launch discount) · `cursor/claude-sonnet-4-6` (strictly
dominated by Sonnet 5 on every price tier, effort caps below `xhigh`).

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
| Human | Credentials, kill tabs, irreversible acts — `needs-attended` + one recommended answer |

## Packet

Six-block handoff packet (`architecture-handoff-protocol`). Front-matter SHOULD
set `packet_kind: conductor` and `role_name: conductor`.

Required in `<scope>` / `<invariants>`:
- **`Use the conductor skill — …`** (continuity-lead required-skill gate — see Audience)
- Root thread id + charter + scoreboard URIs
- Checkout regime (**Lane A** vs **Lane B**) — operator-bound, not inferred
- Incident/sibling lanes (cite ≠ convert)
- Forbidden verbs (e.g. no `request` on a stood-down lane)
- Judgment vs human rule (above)
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
  lane="B",                       # when regime is Lane B — REQUIRED, not optional
  # skills=["conductor"]  # optional document; ¬ mounted on cursor-sdk — Use-line wins
)
```

Preflight: packet Use-line present ∧ `manage(busy_status)` — if the chosen lane's
write lease is held by another dispatch, expect **queued**; record holder on the
root CHECKPOINT. ¬ nest_under an unrelated mission's lease.

**Post-admit check (binding when Lane B):** quote `active_by_lane` / `holder_source_repo`
from `busy_status`. Expected Lane B: `B≥1`, worktree under
`ulg-arc-worktrees/lane-*`, branch `cursor-sdk/lane-*`. If you see `A=1` and
`holder_source_repo=…/universal-llm-gateway` (shared master), the admit landed
on the wrong regime — stop nesting mechanical work and correct before edits.

When admitting **T3 Opus**: one announce line (`Conductor T3: <trigger> — <why>`),
then proceed (`lean-context-dispatch-first` inform-then-proceed).

## Gotchas

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
| Land by `git checkout <branch> -- <paths>` onto master | Merge the lane branch via operator-gated `git_land` (see `git-posture`) |

Scope refuse usually means an absolute mount path, `cortex://` / non-repo URI, or
a path outside the gateway checkout leaked into machine-derived file scope.
Strip those from the conductor packet (and any `source_ref` materializer inputs)
until `lane="B"` admits cleanly.

Nested Composer under a Lane-B conductor must `nest_under=<conductor dispatch_id>`
**and** inherit Lane B — ¬ fire a fresh top-level implement that can mint another
branch or fall onto master.

## Interactive entry

Command `/conductor` (plugin): orient → ask establishing questions (incl. **model
tier**) → draft charter/scoreboard/packet → confirm → admit. Skill body does not
re-ask when the operator already bound the answers in chat.

## Composes with

| Skill / rule | Boundary |
|---|---|
| `orchestration-lanes` | Root/mission birth; conductor **runs** after birth |
| `mission-operator` | Formal operator_proxy turn schema — different lane kind |
| `checkpoint-discipline` | Root CHECKPOINT tip hygiene |
| `handoff-packet-authoring` | Six-block authoring |
| `bind-then-compose-dispatch` | G5 mechanical nest |
| `judgment-escalation-ladder` | Unsure → binder, not human |
| `git-posture` | Lane-B branch land = merge/`git_land`; ¬ path-copy onto master |
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
| Conductor on Lane A while G-row code is on `cursor-sdk/lane-*` | One regime: conductor + nests share the Lane-B worktree/branch |
| Opus-by-default for every conductor | Tier table; T1 Sonnet 5 @ `max` default; Opus only with trigger |
